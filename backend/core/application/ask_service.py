from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.ask_conversation import AskContext, AskConversation, context_for
from core.application.ask_hygiene import HYGIENE_TOOL_GUIDANCE, JiraHygieneTool
from core.application.ask_people import AskPeopleWords, NamelessTool
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import DeliveryScopeService, reads_pod_dates, reads_project
from core.application.flow_metrics_service import (
    FlowMetricsService,
    PortfolioFlowView,
    WorkstreamFlowView,
)
from core.application.forecast_service import ForecastService, ScopeDeliveryView
from core.application.hygiene_service import HygieneService
from core.application.impact_service import (
    DeliveryImpactService,
    ImpactEnd,
    ImpactItemView,
    ImpactPeriod,
    ImpactView,
    PeriodKind,
)
from core.application.people_words import hides_names
from core.application.persona_views import (
    PersonaViewService,
    PortfolioHeatmapView,
    TreeNodeView,
    WorkstreamProgressView,
)
from core.application.portfolio_feed_service import (
    DEFAULT_FEED_SOURCES,
    PortfolioFeedService,
    state_label,
)
from core.application.portfolio_privacy import counted_tree, reads_people
from core.application.profile_service import ProfileService
from core.application.readiness_access import pod_viewer, project_viewer, reads_some_board
from core.application.release_readiness_service import (
    BoardView,
    FindingView,
    ReleaseReadinessService,
)
from core.application.risk_service import RiskService
from core.application.rollup_service import NO_WORK_REASON
from core.application.status_summaries import NO_REPLY_BLOCKER
from core.domain.auth import Principal
from core.domain.delivery import STAGE_LABELS, DeliveryStage
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import CommitmentScope, CommitmentScopeKind, Release, Verdict
from core.domain.graph import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    JsonScalar,
    NodeKind,
    workstreams_in_use,
)
from core.domain.impact import Measure
from core.domain.llm import LlmMessage, LlmRequest, LlmToolCall
from core.domain.profiles import ProfileDefinition
from core.domain.release_readiness import (
    Severity,
    Urgency,
    UrgencyKind,
    name_in_sentence,
    stage_words,
)
from core.domain.risk import DriftFinding, RiskFinding
from core.domain.rollup import FactorKind, Rag, RollupFactor
from core.domain.status import StatusSource
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository
from core.ports.tools import AgentTool

# How an answer reads. Answers used to be a paragraph that restated the
# question, lumped a partial update in with no reply at all, closed on a
# generic "follow up with ..." and cited people by chat id. Each rule stands
# alone so a test can hold the prompt to it. They also called GitLab merge
# requests "merged PRs": the facts, risk rules and flow fields Ask reads say
# pr or pull_request whatever the provider, and Ask cannot tell which one it is.
ANSWER_FORMAT_RULES: tuple[str, ...] = (
    "Shape: one verdict line that answers the question in a few words (e.g. 'Digital "
    "Platform Program is red because:'), then at most 4 bullet lines starting with '• ', "
    "one concrete driver each, then optionally one line on what is fine. 80 words at most.",
    "Name people by display name, issues by key (CHK-8), merge requests by the ref the "
    "data gives (storefront-web !1), and programs, projects, workstreams and pods by name. "
    "One bullet per kind of driver, naming everyone it applies to, e.g. 'Partial updates: "
    "Omar Haddad, Ira Novak · no reply: Hana Kobayashi'.",
    "Call a merge request a merge request or MR, e.g. '2 merged MRs', never a PR or pull "
    "request -- even where a tool's text, type or field name says PR, pr or pull_request.",
    "Never put a raw id in answer: no chat user ids such as U0AA1OMAR01 and no node ids "
    "such as pod-data or program-platform; an issue key is the one id that reads as a "
    "name. Ids go in references only, copied exactly as the tools return them: the id of "
    "every person, issue, program, project, workstream and pod the answer names.",
    "Ground every bullet in the rollup factors and signals the tools return, in their own "
    "terms: 'partial' is a partial update and 'no reply' is no reply, so never merge or "
    "upgrade one into the other; give counts exactly (1 open blocker), and never call "
    "something blocked or late unless a factor or signal says so.",
    "No filler, no preamble, no restating the question, and no recommendations or next "
    "steps unless the question asks what to do.",
    "If data you need is missing or a tool comes back empty, say so in one line instead "
    "of guessing.",
)

# How to look facts up with the Ask tools: shared by a quick answer and by
# each step of an investigation (ask_investigation.py).
ASK_TOOL_GUIDANCE = (
    "You know nothing about this tenant's programs, people or status except what "
    "the provided tools return, so look the facts up before answering. "
    "Questions about health -- what is red, amber, at risk, blocked, stuck or "
    "behind -- are answered from the status and risk tools: call them before you "
    "say any status is unavailable, and say which day the status is for. "
    "Why something has its colour, or needs attention, is answered from "
    "status_reasons: the rollup's own factors, and the open risk and drift signals "
    "beneath it. "
    "Whether a project or pod will make its date is answered from delivery_forecast: "
    "the committed date, history's 50% and 85% dates and the verdict. A colour is not a "
    "forecast, so never say a date will be made or missed unless delivery_forecast says "
    "so, and say when it has too little history to forecast. "
    "How many open requirements have no ETA or due date, and which, is answered from "
    "delivery_forecast's open_without_date and open_without_date_keys, never from release "
    "readiness: a release criterion is not a requirement. "
    "Why a date moved -- what changed, delayed or impacted a project's, release's or "
    "pod's timeline, what scope was added since planning -- is answered from "
    "delivery_impact. Its summary's first line says what is not known (planning not "
    "marked complete, too little history for a likely date, days with no breakdown): "
    "begin the answer with that line, word for word, and never drop it. Then its summary "
    "in its own words -- where the likely date stands, then at most three causes in "
    "working days (scope added, scope removed, team speed) and the requirements behind "
    "them -- then the committed date's moves with who and why, and the team's date. Keep "
    "the three dates apart, as its dates field defines them: the committed date (only a "
    "person moves it), the likely date (the forecast's, likely_by) and the team's own "
    "date (the latest finish date the team gave, ETA or due date, across the open "
    "requirements; say it as 'team's own date'). A team's own date or a likely date that "
    "moved is never "
    "'the project's date moved'. Say 'likely by' for the date, never '85%', 'p85', "
    "'throughput' or 'window'. Never explain a move from colours, briefs or the "
    "forecast's dates alone, and never add up or rank what people added: an item's "
    "added_by belongs to that item only. "
    "Whether a project, release or pod is ready for production -- what is still missing "
    "before it can go live, whether it can be released -- is answered from "
    "release_readiness: name each missing or unsure criterion, blocking ones first, with "
    "what it rests on and the day it is needed by, and never call anything ready unless "
    "release_readiness shows every criterion covered or not applicable. Say what is needed "
    "by when in the words of the criterion's what_is_needed: the criterion is what is "
    "needed, and the issue key in reached_stage_without_it is a requirement that already "
    "reached that stage without it, never something that is needed. "
    "Relationships -- who is assigned to what, who belongs to which pod, what "
    "contains what -- live on edges, so call graph_neighbors before reporting that "
    "something has none, and never tell the user their data is missing or needs "
    "updating when you have not traversed its edges. "
    "Who is the security, QA, product or other person for a pod or project is answered "
    "from team_profiles: each member's profile in each pod, and the profiles a project's "
    "release criteria ask for that none of its pods holds. A profile is a role in that pod, "
    "never a rating, and grants no rights. "
    + HYGIENE_TOOL_GUIDANCE
    + "What blocks a project or pod is answered from pod_blockers: for each pod it names "
    "or works through, or for every pod with pod_id unset. Never say anything has no "
    "blockers, no risks or nothing missing unless a tool you called says so. "
    "An inferred day in pod_checkins says what moved in Jira and Git (its evidence); it is "
    "not the person's word, so never say they reported it. "
    "Never mention raw DM/reply content. "
)

# The rounds a quick answer's tool loop may take before it must answer
# (OPENPROGRAM_ASK_MAX_TOOL_ITERATIONS). Three ran out on "What is blocking
# <project>?" in QA round V2: finding the project, its pods and their
# blockers, with one look at its status first, is four. Five leaves one more
# for following a blocker to its owner or issue; a round is one model call of
# a few seconds, so even all five stay far inside the 120-second budget.
ASK_MAX_TOOL_ITERATIONS = 5

# Said to the model when its tool rounds ran out (ToolCallingAgent.cap_notice).
ASK_CAP_NOTICE = (
    "You have run out of tool steps. A tool result that says 'Not run' is a read that did "
    "not happen, so nothing is known of what it would have shown. Answer from the results "
    "you have, and make the answer's first line say what you could not check, e.g. 'I ran "
    "out of steps before checking every pod's open blockers.' Never state a negative you "
    "did not read -- no blockers, no risks, nothing missing -- and never fill the gap from "
    "a colour or from the conversation."
)

# Earlier turns, when the console sends them (ask_conversation.py).
CONVERSATION_GUIDANCE = (
    "Earlier turns of the conversation, when given, say what the question refers to -- "
    "'it', 'they', 'and the other project?' -- but they are not facts for today: look "
    "the facts up again with the tools rather than repeating an earlier answer. "
)

# What the console offers to ask next, below the answer.
FOLLOW_UPS_FIELD = (
    "follow_ups, an array of up to 3 short questions -- 10 words at most, each about one "
    "project, pod, person or issue the answer names -- that the asker may want to ask "
    "next, each answerable from this delivery data and none already asked in the "
    "conversation."
)
MAX_FOLLOW_UPS = 3
_MAX_FOLLOW_UP_CHARS = 140
# A reply once ended its answer on a 'follow_ups: ["...", ...]' line and the
# reader saw the list raw, so the reply's other fields are named as fields.
OWN_FIELDS_RULE = (
    "answer holds the answer's own lines and nothing else: references and follow_ups are "
    "fields of the object beside it, so never write them, or a 'follow_ups:' or "
    "'References:' line, inside answer."
)

ASK_SYSTEM_PROMPT = (
    "You answer program-management questions over a delivery graph. "
    + ASK_TOOL_GUIDANCE
    + CONVERSATION_GUIDANCE
    + "Write the answer to these rules: "
    + " ".join(ANSWER_FORMAT_RULES)
    + " "
    "Once you have the facts, reply with a single JSON object and nothing else: "
    "answer holds the text, with a newline between lines, references an array "
    "of the node ids it rests on, and " + FOLLOW_UPS_FIELD + " " + OWN_FIELDS_RULE
)

# Named periods a time-window question maps onto, resolved against the as-of
# date the question is asked for -- never the host's clock or the model's.
FACT_PERIODS = ("today", "yesterday", "this_week", "last_week", "last_7_days", "last_30_days")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MAX_FACT_WINDOW_DAYS = 31
# The feed reads newest first with no upper bound, so a window ending before
# today is cut from a bounded scan rather than an unbounded read.
_FACT_SCAN_LIMIT = 500
_MAX_PODS = 20
_MAX_TEXT_CHARS = 240
_MAX_REASONS = 15
_MAX_PARTS = 20
_MAX_SIGNALS = 10
_MAX_PROJECTS = 20
# The undated requirements a forecast names; open_without_date still counts them all.
_MAX_UNDATED_KEYS = 25
# The projects a pod's delivery_impact covers: one scope's breakdown each.
_MAX_IMPACT_PROJECTS = 3
_RAG_ORDER: dict[Rag, int] = {Rag.RED: 0, Rag.AMBER: 1, Rag.UNKNOWN: 2, Rag.GREEN: 3}
_AS_OF_PARAMETER: Mapping[str, object] = {
    "type": "string",
    "format": "date",
    "description": "An earlier day to look at. Leave unset for today.",
}

# Whose reasons a principal may read, per node kind: the capabilities the REST
# route serving those reasons checks -- /programs/{id}/tree,
# /projects/{id}/progress, /workstreams/{id}/progress and /pods/{id}/rollup.
_REASON_CAPABILITIES: Mapping[NodeKind, tuple[Capability, ...]] = {
    NodeKind.PROGRAM: (Capability.READ_PROGRAM_ROLLUP,),
    NodeKind.PROJECT: (Capability.READ_PROJECT_PROGRESS,),
    NodeKind.WORKSTREAM: (Capability.READ_PROJECT_PROGRESS,),
    NodeKind.POD: (Capability.READ_POD_BLOCKERS, Capability.READ_POD_CHECKINS),
}

# A person's check-in state in the words an answer may use. The collector
# writes an inferred, stale or unknown status only when someone has not
# answered a check-in, so those read "no reply"; unknown also covers a person
# nothing was ever recorded for, so it claims no more than "no status".
_CHECKIN_WORDS: Mapping[StatusSource, str] = {
    StatusSource.CONFIRMED: "confirmed",
    StatusSource.PARTIAL: "partial",
    StatusSource.INFERRED: "no reply",
    StatusSource.STALE: "no reply",
    StatusSource.UNKNOWN: "no status",
}
_NO_REPLY_SAYS = "No confirmed reply to the check-in."
# A forecast's verdict as the day report words it (frontend-v3 VERDICT_WORDS).
_VERDICT_WORDS: Mapping[Verdict, str] = {
    Verdict.ON_TRACK: "on track",
    Verdict.AT_RISK: "at risk",
    Verdict.OFF_TRACK: "off track",
    Verdict.DONE: "done",
    Verdict.NO_DATE: "no delivery date set",
    Verdict.NOT_ENOUGH_DATA: "not enough history to forecast",
}
# A scope with no requirements counted has no verdict worth giving, as the console
# shows it: its forecast reads "done" only because nothing is open.
_NOTHING_TO_FORECAST = "nothing to forecast: no requirements counted"
_NOTHING_COUNTED_REASON = "No requirements are counted for it yet."
# What a verdict was judged from, for the verdicts judged against a date.
_JUDGED: frozenset[Verdict] = frozenset({Verdict.ON_TRACK, Verdict.AT_RISK, Verdict.OFF_TRACK})
_TARGET_SOURCES: Mapping[str, str] = {
    "committed": "committed date",
    "jira_release": "Jira release date",
}
_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+")
_BULLET = re.compile(r"^\s*[-*•]\s+")

# The model is asked for a JSON object, yet a live reply can come back as
# prose ending in a "References: [...]" line, the object can carry raw
# newlines inside its strings or be cut off, and the answer can restate its
# references. Each shape is taken apart here, so the reader always gets the
# answer's own lines and the references always reach sources.
_JSON = json.JSONDecoder(strict=False)
_MAX_OBJECT_STARTS = 50
_REFERENCE_WORD = r"(?:references?|refs|sources?|citations?)"
# Only the plural ends a line, so "(source: Jira)" in a bullet stays.
_REFERENCES_WORD = r"(?:references|refs|sources|citations)"
# "References: a, b", "**Sources:** [a, b]", "- References (node ids):".
_REFERENCES_LINE = re.compile(
    rf"^\s*(?:[-*•]\s*)?[*_`]*{_REFERENCE_WORD}(?:\s*\([^)]*\))?[*_`]*\s*[:：]\s*[*_`]*"
    r"(?P<items>.*?)[*_`]*\s*$",
    re.IGNORECASE,
)
# "... green. (References: a, b)" or "... green. [Sources: a, b]".
_TRAILING_REFERENCES = re.compile(
    rf"\s*[(\[]\s*{_REFERENCES_WORD}\s*[:：]\s*(?P<items>[^()\[\]]*)[)\]]\s*\.?\s*$",
    re.IGNORECASE,
)
# "... green. References: [a, b]".
_TRAILING_REFERENCE_LIST = re.compile(
    rf"\s*\b{_REFERENCES_WORD}\s*[:：]\s*(?P<items>\[[^\[\]]*\])\s*\.?\s*$",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?P<item>.+?)\s*$")
_ANSWER_LABEL = re.compile(r"^\s*[*_]*answer[*_]*\s*[:：]\s*", re.IGNORECASE)
_FENCE_LINE = re.compile(r"^\s*```[\w-]*\s*$")
# A JSON reply that does not decode: cut off, or with a trailing comma.
_ANSWER_FIELD = re.compile(r'"answer"\s*:\s*"(?P<text>(?:[^"\\]|\\.)*)', re.IGNORECASE)
_FOLLOW_UPS_FIELD = re.compile(r'"follow_?ups"\s*:\s*(?P<items>\[[^\]]*\]?)', re.IGNORECASE)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
# A keyless string after another: '", "• Blocker: CHK-8"' followed by ',' or '}',
# so never a key (a key is followed by ':').
_LOOSE_LINE = re.compile(r'"\s*,\s*"(?P<text>(?:[^"\\]|\\.)*)"(?=\s*[,}])')
_REFERENCES_FIELD = re.compile(
    r'"(?:references|sources)"\s*:\s*(?P<items>\[[^\]]*\]?)', re.IGNORECASE
)
_ITEM_SPLIT = re.compile(r"\s*(?:[,;\n]|\s·\s)\s*")
_ITEM_EDGES = " \t\"'`*_"
# A follow-ups list written into the answer instead of beside it, as Ora once
# showed one: 'follow_ups: ["What is Noah Weber\'s workload?", ...]' under the
# answer. The field's own name marks the list in any spelling -- follow_ups,
# followUps, follow_up_questions, quoted as a JSON key -- and so does a heading
# in words ("**Follow-up questions:**", "Suggested follow-ups:"), but only over
# questions: a singular "Follow up:" or a "Follow-ups: 2 open" line is the
# answer's own.
_FOLLOW_UPS_KEY = r"follow_?ups|follow_?up_?questions"
_FOLLOW_UPS_WORDS = (
    r"(?:suggested\s+|next\s+)?follow[\s-]ups|follow[\s-]up\s+questions"
    r"|(?:next|suggested)\s+questions|questions\s+to\s+ask\s+next"
)
_FOLLOW_UPS_HEADING = re.compile(
    r"^\s*(?:[-*•]\s*)?[*_`\"']*"
    rf"(?:(?P<field>{_FOLLOW_UPS_KEY})|(?P<words>{_FOLLOW_UPS_WORDS}))"
    r"[*_`\"']*\s*[:：=]\s*[*_`]*(?P<items>.*?)[*_`]*\s*,?\s*$",
    re.IGNORECASE,
)
# "... for this task. follow_ups: [...]", or a '{"follow_ups": [...]}' fragment.
_TRAILING_FOLLOW_UPS = re.compile(
    r"\s*\{?\s*[\"']?\b(?:" + _FOLLOW_UPS_KEY + r")[\"']?\s*[:：=]\s*"
    r"(?P<items>\[[^\[\]]*\]?)\s*\}?\s*[,.]?\s*$",
    re.IGNORECASE,
)
_AFTER_QUESTION = re.compile(r"(?<=\?)[\"'`]?[\s,;]+")

# An answer that says it could not check everything, in the words the cap
# notice asks for or close to them.
_RAN_OUT = re.compile(
    r"\bran out of (?:tool )?steps\b"
    r"|\bcould(?: not|n't) (?:check|look up|read|be (?:checked|looked up|read))\b",
    re.IGNORECASE,
)
# Claims that something is absent, each with the reads that can show it. One
# of them stands in an answer written at the cap only when a read of its kind
# ran and none was left unrun; otherwise the sentence goes. "<Project> has no
# blockers reported today" came 2 seconds after the cap in QA round V2, with a
# blocker open in one of its pods.
_ABSENT_QUALIFIERS = r"(?:(?:open|active|current|known|reported|new|other|outstanding)\s+)*"
_UNREAD_NEGATIVES: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = (
    (
        frozenset({"pod_blockers", "status_reasons"}),
        re.compile(
            rf"\b(?:no|zero|0)\s+{_ABSENT_QUALIFIERS}block(?:ers?\b|ing\b|ed\b)"
            r"|\bnothing\s+(?:is\s+)?(?:currently\s+)?block(?:ing|ed)\b"
            r"|\b(?:no\s*one|nobody|none)\b[^.;\n]{0,40}\bblocked\b"
            r"|(?:\bnot|n't)\s+(?:currently\s+)?blocked\b|\bunblocked\b"
            r"|(?:\bnot|n't)\b[^.;\n]{0,15}\bany\s+blockers?\b"
            r"|\bwithout\s+(?:any\s+)?blockers?\b|\bblockers?\s*[:：]\s*(?:none|0)\b",
            re.IGNORECASE,
        ),
    ),
    (
        frozenset({"open_risks", "status_reasons"}),
        re.compile(
            rf"\bno\s+{_ABSENT_QUALIFIERS}risks?\b|\bnothing\s+(?:is\s+)?at\s+risk\b"
            r"|(?:\bnot|n't)\s+at\s+risk\b|\brisks?\s*[:：]\s*(?:none|0)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # What moved a date: "no scope was added" stands only on a finished read of it.
        frozenset({"delivery_impact"}),
        re.compile(
            rf"\bno\s+{_ABSENT_QUALIFIERS}(?:scope|requirements?|change\s+requests?|additions?)"
            r"\s+(?:(?:was|were|has\s+been|have\s+been)\s+)?(?:added|removed)\b"
            r"|\bnothing\s+(?:was\s+|has\s+been\s+)?(?:added|removed)\b"
            r"|\bno\s+scope\s+(?:changes?|creep)\b",
            re.IGNORECASE,
        ),
    ),
    (
        frozenset({"release_readiness"}),
        re.compile(
            r"\bnothing\s+(?:else\s+)?(?:is\s+)?(?:still\s+)?missing\b"
            rf"|\bno\s+{_ABSENT_QUALIFIERS}(?:gaps?|criteria\s+(?:are\s+)?missing)\b"
            r"|\bready\s+(?:for|to\s+go\s+to)\s+production\b",
            re.IGNORECASE,
        ),
    ),
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# The words for what a read covers, for "I ran out of steps before checking ...".
_READ_WORDS: Mapping[str, str] = {
    "search_graph_nodes": "a name look-up",
    "graph_neighbors": "the links",
    "recent_facts": "recent activity",
    "workstream_flow": "the flow",
    "portfolio_flow": "the portfolio's flow",
    "workstream_progress": "the progress",
    "status_reasons": "the status reasons",
    "portfolio_heatmap": "the portfolio heatmap",
    "open_risks": "the open risks",
    "pod_checkins": "the check-ins",
    "pod_blockers": "the open blockers",
    "delivery_forecast": "the delivery forecast",
    "delivery_impact": "what moved the date",
    "release_readiness": "the release readiness",
    "team_profiles": "the profiles",
    "jira_hygiene": "the Jira hygiene",
}
_EVERY_POD_READS = frozenset({"pod_checkins", "pod_blockers"})
_NODE_ARGUMENTS = ("node_id", "pod_id", "project_id", "workstream_id", "program_root_id")
_MAX_LOOKUP_CHARS = 60
# Text in brackets inside one item: "Zoe Almeida (U0AA1ZOE001)".
_BRACKETED = re.compile(r"[(\[]\s*(?P<inner>[^()\[\]]+?)\s*[)\]]")
# The node kinds an answer names, as the references rule lists them.
_NAMED_KINDS = frozenset(
    {
        NodeKind.PROGRAM,
        NodeKind.PROJECT,
        NodeKind.WORKSTREAM,
        NodeKind.POD,
        NodeKind.DEVELOPER,
        NodeKind.TASK,
        NodeKind.WORK_ITEM,
    }
)
_MIN_NAMED_LABEL = 3
# What the answer says when the reply held no answer text at all.
_NO_ANSWER = "No answer came back for this question. Please ask again."


@dataclass(frozen=True, kw_only=True)
class DateWindow:
    """An inclusive run of days."""

    start: date
    end: date


def period_windows(as_of: date) -> dict[str, DateWindow]:
    """Every named period, resolved against the day the question is asked for."""
    monday = as_of - timedelta(days=as_of.weekday())
    yesterday = as_of - timedelta(days=1)
    return {
        "today": DateWindow(start=as_of, end=as_of),
        "yesterday": DateWindow(start=yesterday, end=yesterday),
        "this_week": DateWindow(start=monday, end=as_of),
        "last_week": DateWindow(start=monday - timedelta(days=7), end=monday - timedelta(days=1)),
        "last_7_days": DateWindow(start=as_of - timedelta(days=6), end=as_of),
        "last_30_days": DateWindow(start=as_of - timedelta(days=29), end=as_of),
    }


@dataclass(frozen=True, kw_only=True)
class AskSource:
    """One reference, with the words a reader knows it by.

    ``label`` is a person's display name, an issue's key, a merge request's
    ref, or a program, project, workstream or pod name. It is None when the id
    matches no node -- a reference the model got wrong stays an id, never a
    guessed name.
    """

    id: str
    kind: NodeKind | None
    label: str | None


@dataclass(frozen=True, kw_only=True)
class AskResponseView:
    answer: str
    references: tuple[str, ...]
    tools_used: tuple[str, ...]
    trace_id: str
    sources: tuple[AskSource, ...] = ()
    # Up to MAX_FOLLOW_UPS questions to offer next, in readable words.
    follow_ups: tuple[str, ...] = ()
    # Set when the conversation was compacted: the new summary, and how many of
    # the turns sent it now covers (ask_conversation.AskContext).
    summary: str | None = None
    summarized_turns: int = 0


@dataclass(frozen=True, kw_only=True)
class ParsedAnswer:
    """The model's reply taken apart, before any node is looked up.

    ``references`` is the references array of a JSON reply, as the model sent
    it. ``cited`` is what a "References: [...]" line listed instead -- a line
    cut out of ``answer``, so it is never shown -- still to be matched to nodes.
    """

    answer: str
    references: tuple[str, ...]
    cited: tuple[str, ...] = ()
    follow_ups: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SearchGraphNodesTool:
    """Name to id, over the nodes the views show.

    Workstreams are optional: one holding no task or work item on ``as_of``
    is left out, as the navigator and heat rows leave it out, so a list of
    workstreams never names an empty one. Asked for by its exact id or name,
    it still comes back, marked not in use with the reason, so Ask says it
    holds no work rather than that it does not exist.
    """

    tenant_id: str
    repository: GraphRepository
    as_of: date

    name: str = "search_graph_nodes"
    description: str = (
        "Find graph nodes -- programs, projects, workstreams, pods, developers, tasks "
        "and work items -- by text in their id, name or metadata, optionally of given "
        "kinds. Returns each match's id, kind, name and metadata, but no status. Use it "
        "to turn a name into the id the other tools take."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search across name, id, and metadata.",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [kind.value for kind in NodeKind]},
                    "description": "Optional node kinds to restrict the search to.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximum number of matches to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        query = _string_argument(arguments.get("query"))
        kinds = _kinds_argument(arguments.get("kinds"))
        limit = _bounded_int(arguments.get("limit"), default=10, maximum=25)
        nodes = await self.repository.list_nodes(self.tenant_id, as_of=self.as_of)
        in_use = workstreams_in_use(
            nodes,
            await self.repository.list_edges(self.tenant_id, kind=EdgeKind.CONTAINS),
            self.as_of,
        )
        if kinds:
            kind_set = {NodeKind(kind) for kind in kinds}
            nodes = [node for node in nodes if node.kind in kind_set]
        query_value = (query or "").lower()
        if query_value:
            nodes = [
                node
                for node in nodes
                if query_value in node.id.lower()
                or query_value in node.name.lower()
                or _metadata_text(node).find(query_value) >= 0
            ]
        nodes = [
            node
            for node in nodes
            if node.kind is not NodeKind.WORKSTREAM
            or node.id in in_use
            or query_value in {node.id.lower(), node.name.lower()}
        ]
        matches = [_search_match(node, in_use) for node in nodes[:limit]]
        return json.dumps(matches, ensure_ascii=False)


# The details keys that hold a tracker or work-item state ("in_progress").
_STATE_DETAILS = ("state", "from_state", "to_state")


def _spoken_states(details: Mapping[str, JsonScalar]) -> dict[str, JsonScalar]:
    """A fact's details with its states in words ("In progress", not "in_progress").

    A model that is handed ``in_progress`` writes it into the answer as it came.
    """
    return {
        key: state_label(value)
        if key in _STATE_DETAILS and isinstance(value, str) and value
        else value
        for key, value in details.items()
    }


def _search_match(node: GraphNode, in_use: frozenset[str]) -> dict[str, object]:
    match: dict[str, object] = {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "metadata": dict(node.metadata),
    }
    if node.kind is NodeKind.WORKSTREAM and node.id not in in_use:
        match["in_use"] = False
        match["note"] = NO_WORK_REASON
    return match


@dataclass(frozen=True, kw_only=True)
class GraphNeighborsTool:
    """Edge traversal, without which no relationship question is answerable.

    `search_graph_nodes` finds a node but says nothing about what it connects
    to, so questions like "which tasks is this developer on" used to come back
    as "no tasks found in the current graph" -- blaming correct data for a
    missing tool. A workstream holding no work on ``as_of`` is no neighbor:
    workstreams are optional, and the views leave an empty one out.
    """

    tenant_id: str
    repository: GraphRepository
    as_of: date

    name: str = "graph_neighbors"
    description: str = (
        "List the edges touching one graph node, with the node at the other end "
        "resolved to its kind and name. Use this for any question about how things "
        "relate: which tasks or work items a developer is assigned to, who belongs "
        "to a pod, which pod or workstream holds an item, or what a project "
        "contains. Edge kinds are 'contains' (parent to child, including pod to "
        "member), 'assigned_to' (developer to task, pod to workstream) and "
        "'depends_on'. Call search_graph_nodes first if you only know a name."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": (
                        "Node whose edges to list, e.g. a developer id like U1004, "
                        "a pod id, or a work item key."
                    ),
                },
                "direction": {
                    "type": "string",
                    "enum": ["out", "in", "both"],
                    "description": (
                        "'out' for edges leaving the node (a developer's "
                        "assignments), 'in' for edges arriving at it (the pod that "
                        "contains a developer). Defaults to 'both'."
                    ),
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional edge kinds to keep, e.g. ['assigned_to'].",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of edges to return.",
                },
            },
            "required": ["node_id"],
            "additionalProperties": False,
        }
    )

    def _wanted(self, edge: GraphEdge, kinds: set[EdgeKind]) -> bool:
        """Active on the asked-for date, and of a kind the caller wants."""
        return edge.is_active_on(self.as_of) and (not kinds or edge.kind in kinds)

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        node_id = _string_argument(arguments.get("node_id"))
        if node_id is None:
            return json.dumps({"error": "node_id is required"})
        direction = _string_argument(arguments.get("direction")) or "both"
        if direction not in {"out", "in", "both"}:
            direction = "both"
        kinds = set(_edge_kinds_argument(arguments.get("kinds")))
        limit = _bounded_int(arguments.get("limit"), default=25, maximum=50)

        found: list[tuple[str, str, str]] = []
        if direction in {"out", "both"}:
            for edge in await self.repository.list_edges(self.tenant_id, from_node_id=node_id):
                if self._wanted(edge, kinds):
                    found.append(("out", edge.kind.value, edge.to_node_id))
        if direction in {"in", "both"}:
            for edge in await self.repository.list_edges(self.tenant_id, to_node_id=node_id):
                if self._wanted(edge, kinds):
                    found.append(("in", edge.kind.value, edge.from_node_id))

        nodes = await self.repository.list_nodes(self.tenant_id, as_of=self.as_of)
        # An empty workstream is optional set-up, no relation worth naming.
        in_use = workstreams_in_use(
            nodes,
            await self.repository.list_edges(self.tenant_id, kind=EdgeKind.CONTAINS),
            self.as_of,
        )
        empty = {
            node.id for node in nodes if node.kind is NodeKind.WORKSTREAM and node.id not in in_use
        }
        found = [item for item in found if item[2] not in empty][:limit]
        wanted = {neighbor for _, _, neighbor in found}
        names = {node.id: node for node in nodes if node.id in wanted}
        node = await self.repository.get_node(self.tenant_id, node_id, as_of=self.as_of)
        return json.dumps(
            {
                "node": (
                    {"id": node.id, "kind": node.kind.value, "name": node.name}
                    if node is not None
                    else {"id": node_id}
                ),
                "as_of": self.as_of.isoformat(),
                "edges": [
                    {
                        "direction": edge_direction,
                        "edge_kind": edge_kind,
                        "neighbor_id": neighbor_id,
                        "neighbor_kind": (
                            names[neighbor_id].kind.value if neighbor_id in names else None
                        ),
                        "neighbor_name": (
                            names[neighbor_id].name if neighbor_id in names else None
                        ),
                    }
                    for edge_direction, edge_kind, neighbor_id in found
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class RecentFactsTool:
    """What happened over a window of days that ends no later than the as-of date.

    It used to count back from the host's clock, so a question asked as of an
    earlier day read the wrong week, and "since Monday" left the model to do
    date arithmetic in a ``since_days`` integer.
    """

    tenant_id: str
    repository: TimeSeriesRepository
    as_of: date

    name: str = "recent_facts"
    description: str = (
        "The activity log, newest first: work-item transitions, issue updates, commits, "
        "merge requests (MRs), check-in updates (counts only, never reply text), risks opened "
        "or cleared, and cross-person requests. Covers a window of days ending no later "
        "than today; pick it with period, or with since/until dates, and it defaults to "
        "the last 7 days. Use it for what changed or happened over a period -- 'since "
        "Monday', 'yesterday', 'this week' -- not for current status."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": list(FACT_PERIODS),
                    "description": (
                        "A named window, resolved against today. Weeks start on Monday, "
                        "so this_week runs from Monday to today."
                    ),
                },
                "since": {
                    "type": "string",
                    "format": "date",
                    "description": "First day of the window, when no period fits.",
                },
                "until": {
                    "type": "string",
                    "format": "date",
                    "description": "Last day of the window. Defaults to today.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(DEFAULT_FEED_SOURCES)},
                    "description": (
                        "Optional fact sources to keep; vcs_pull_request holds the merge "
                        "requests (MRs)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of facts to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        window = fact_window(arguments, self.as_of)
        limit = _bounded_int(arguments.get("limit"), default=20, maximum=50)
        sources = _string_list_argument(arguments.get("sources"))
        since = datetime.combine(window.start, time.min, tzinfo=UTC)
        until = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=UTC)
        feed = PortfolioFeedService(self.repository)
        view = await feed.feed(
            self.tenant_id, since=since, sources=sources or None, limit=_FACT_SCAN_LIMIT
        )
        items = [item for item in view.items if item.observed_at < until]
        return json.dumps(
            {
                "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
                "fact_count": len(items),
                # A full scan may have stopped short of the window's oldest facts.
                "may_be_incomplete": len(view.items) >= _FACT_SCAN_LIMIT,
                "facts": [
                    {
                        "source": item.source,
                        "kind": item.kind,
                        "summary": item.summary,
                        "entity_kind": item.entity_ref.kind.value,
                        "entity_id": item.entity_ref.id,
                        "observed_at": item.observed_at.isoformat(),
                        "details": _spoken_states(item.details),
                    }
                    for item in items[:limit]
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class WorkstreamFlowTool:
    tenant_id: str
    service: FlowMetricsService
    as_of: date

    name: str = "workstream_flow"
    description: str = (
        "Flow metrics for one workstream on one day, today unless as_of names an "
        "earlier one: active, in-flight, completed, stale and abandoned work-item "
        "counts, average cycle time and merge request (MR) age, and each work item's state "
        "and age. "
        "It describes movement and carries no RAG status."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["workstream_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        workstream_id = _required_string(arguments.get("workstream_id"), "workstream_id")
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        view = await self.service.workstream_flow(self.tenant_id, workstream_id, as_of)
        return json.dumps(_workstream_flow_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class PortfolioFlowTool:
    tenant_id: str
    service: FlowMetricsService
    as_of: date

    name: str = "portfolio_flow"
    description: str = (
        "Flow metrics for every workstream on one day, today unless as_of names an "
        "earlier one: active, in-flight, completed, stale and abandoned counts, and "
        "average cycle time and merge request (MR) age. It describes movement and carries "
        "no RAG status."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "as_of": _AS_OF_PARAMETER,
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        view = await self.service.portfolio_flow(self.tenant_id, as_of)
        return json.dumps(_portfolio_flow_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class WorkstreamProgressTool:
    tenant_id: str
    service: PersonaViewService
    as_of: date

    name: str = "workstream_progress"
    description: str = (
        "RAG status of one workstream on one day, today unless as_of names an earlier "
        "one: its colour, source and confidence, percent complete, task counts by "
        "colour, the factors behind the colour, and its red and amber tasks. Use it to "
        "say whether, and why, a workstream is at risk."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["workstream_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        workstream_id = _required_string(arguments.get("workstream_id"), "workstream_id")
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        view = await self.service.workstream_progress(self.tenant_id, workstream_id, as_of)
        return json.dumps(_workstream_progress_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class StatusReasonsTool:
    """Why a program, project, workstream or pod has its colour, in the rollup's words.

    Without it Ask saw one clipped "why" per heatmap cell, so "why does the
    program need attention?" came back as "multiple unresolved issues and
    unconfirmed statuses": two people who had replied in part lumped in with
    one who had not replied at all, and the actual blocker buried. This hands
    over every factor behind the colour, grouped as the Delivery panel groups
    them, each naming the person or issue it comes from -- and a person's
    check-in state in the words the data supports.

    The open risk and drift signals on anything beneath the node come along:
    the model was told to call open_risks beside it and, asked why the
    program needed attention, never did -- so two issues merged but still
    open in the tracker went unmentioned.

    ``kinds`` are the node kinds this principal may read reasons for: a kind
    whose REST route would refuse them comes back as an error, never data.
    The signals are the ones open_risks lists, behind the same aggregate read
    /ask itself requires, so carrying them here widens nothing.
    """

    tenant_id: str
    service: PersonaViewService
    risks: RiskService
    repository: GraphRepository
    as_of: date
    kinds: frozenset[NodeKind]
    #: The reader counts people only (an executive): the tree as /programs/{id}/tree
    #: gives it them, nobody's node and no one's own status among the factors (D103).
    counts_only: bool = False

    name: str = "status_reasons"
    description: str = (
        "Why one program, project, workstream or pod has its RAG colour on one day, today "
        "unless as_of names an earlier one: every rollup factor behind the colour, worst "
        "first, in the rollup's own words. A factor names the people and issues it comes "
        "from, and a status factor carries their check-in state as the data has it: "
        "'partial' (replied without confirming blockers or ETA), 'no reply' (did not "
        "answer the check-in) or 'no status'. It also lists the colour of every project, "
        "workstream and pod underneath, and the open risk and drift signals on anything "
        "underneath -- such as an issue still open after its merge request merged. Use it "
        "for 'why is X red or amber' and 'why does X need attention'; search_graph_nodes "
        "turns a name into the node_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The program, project, workstream or pod to explain.",
                },
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["node_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        node_id = _required_string(arguments.get("node_id"), "node_id")
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        node = await self.repository.get_node(self.tenant_id, node_id, as_of=as_of)
        if node is None:
            raise GraphNotFound(
                f"{node_id} not found; search_graph_nodes finds a program, project, "
                "workstream or pod by name"
            )
        if node.kind not in _REASON_CAPABILITIES:
            return json.dumps(
                {
                    "error": f"{node_id} is a {node.kind.value}; status_reasons explains a "
                    "program, project, workstream or pod"
                }
            )
        if node.kind not in self.kinds:
            return json.dumps(
                {"error": f"the reasons behind a {node.kind.value}'s status are not available"}
            )
        tree = await self.service.program_tree(self.tenant_id, node_id, as_of)
        if self.counts_only:
            tree = counted_tree(tree)
        labels = {
            graph_node.id: node_label(graph_node)
            for graph_node in await self.repository.list_nodes(self.tenant_id, as_of=as_of)
        }
        beneath = {tree_node.id for tree_node in tree.nodes}
        risks = [
            risk
            for risk in await self.risks.portfolio_risks(self.tenant_id, as_of)
            if risk.entity_ref.id in beneath
        ]
        drift = [
            finding
            for finding in await self.risks.portfolio_drift(self.tenant_id, as_of)
            if finding.entity_ref.id in beneath
        ]
        signals = [
            (
                finding.severity,
                _signal_payload(
                    "drift",
                    finding.kind.value,
                    finding.severity,
                    finding.entity_ref.id,
                    finding.owner_id,
                    finding.reason,
                    labels,
                ),
            )
            for finding in drift
        ] + [
            (
                risk.severity,
                _signal_payload(
                    "risk",
                    risk.rule_id.value,
                    risk.severity,
                    risk.entity_ref.id,
                    risk.owner_id,
                    risk.reason,
                    labels,
                ),
            )
            for risk in risks
        ]
        signals.sort(key=lambda signal: _RAG_ORDER[signal[0]])  # worst first
        payload = _status_reasons_payload(tree.root_id, tree.nodes, labels, as_of)
        payload["open_signal_count"] = len(signals)
        payload["signals"] = [signal for _, signal in signals[:_MAX_SIGNALS]]
        return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class PortfolioHeatmapTool:
    tenant_id: str
    service: PersonaViewService
    repository: GraphRepository
    as_of: date
    #: The asker sees no person (an executive): the route's own projection (D87).
    counts_only: bool = False
    asker_id: str | None = None

    name: str = "portfolio_heatmap"
    description: str = (
        "RAG status of everything in the portfolio -- programs, projects, workstreams, "
        "pods, developers and tasks -- on one day, today unless as_of names an earlier "
        "one. Worst first, each with its colour, source and the main reason for it. "
        "This is the status behind the dashboard heat rows: use it for 'what is at "
        "risk', 'what is red or amber' and 'which workstreams are behind'. Narrow it "
        "with kinds, e.g. ['workstream']. It gives one reason per node; for why one "
        "node has its colour, status_reasons gives all of them."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "as_of": _AS_OF_PARAMETER,
                "program_root_id": {"type": "string"},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [kind.value for kind in NodeKind]},
                    "description": "Optional node kinds to keep.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of cells to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        program_root_id = _string_argument(arguments.get("program_root_id"))
        kinds = set(_kinds_argument(arguments.get("kinds")))
        limit = _bounded_int(arguments.get("limit"), default=60, maximum=100)
        view = await self.service.portfolio_heatmap(
            self.tenant_id,
            as_of,
            program_root_id,
            counts_only=self.counts_only,
            reader_id=self.asker_id,
        )
        names = await _node_names(self.repository, self.tenant_id, as_of)
        return json.dumps(
            _portfolio_heatmap_payload(view, names, kinds=kinds, limit=limit),
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class OpenRisksTool:
    """The Signals list, so "what are the top risks?" reads what the screen shows."""

    tenant_id: str
    service: RiskService
    repository: GraphRepository
    as_of: date

    name: str = "open_risks"
    description: str = (
        "The risks and drift findings open today -- the list the Signals screen shows. "
        "A risk is signal-derived (a feature with no merge request (MR), an ageing MR, a "
        "stale work item) and carries its severity, days open, the reason, its evidence, what the "
        "owner's latest check-in says, and whether it is a watermelon (the owner "
        "reports fine while the signals do not). A drift finding is where a stated "
        "status and the hard signals disagree. Use it for 'top risks', 'what is at "
        "risk or stuck' and 'where do owners and signals disagree'. Narrow it with "
        "project_id or workstream_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "workstream_id": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "Maximum number of risks, and of drift findings.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        project_id = _string_argument(arguments.get("project_id"))
        workstream_id = _string_argument(arguments.get("workstream_id"))
        limit = _bounded_int(arguments.get("limit"), default=15, maximum=30)
        if project_id is None:
            risks = await self.service.portfolio_risks(self.tenant_id, self.as_of)
            drift = await self.service.portfolio_drift(self.tenant_id, self.as_of)
        else:
            risks = await self.service.project_risks(self.tenant_id, project_id, self.as_of)
            drift = await self.service.project_drift(self.tenant_id, project_id, self.as_of)
        if workstream_id is not None:
            risks = [risk for risk in risks if risk.workstream_id == workstream_id]
            drift = [finding for finding in drift if finding.workstream_id == workstream_id]
        names = await _node_names(self.repository, self.tenant_id, self.as_of)
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "risk_count": len(risks),
                "drift_count": len(drift),
                "risks": [_risk_payload(risk, names) for risk in risks[:limit]],
                "drift": [_drift_payload(finding, names) for finding in drift[:limit]],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class PodCheckinsTool:
    tenant_id: str
    service: PersonaViewService
    repository: GraphRepository
    as_of: date

    name: str = "pod_checkins"
    description: str = (
        "Who in a pod has checked in today: each developer's state (confirmed, "
        "partial, stale or missing), the day their status was last given and its "
        "parsed summary -- never the reply itself -- with counts per state. An "
        "inferred day carries evidence: the lines of what moved in the tracker and "
        "Git it was inferred from, not the person's word. coverage counts how the "
        "pod's day was captured (replied, inferred, no data and why, waiting). Omit "
        "pod_id to cover every pod."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"pod_id": {"type": "string"}},
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        pods = await _pods(self.repository, self.tenant_id, arguments.get("pod_id"))
        if isinstance(pods, str):
            return json.dumps({"error": pods})
        views = [
            await self.service.pod_checkins(self.tenant_id, pod.id, self.as_of) for pod in pods
        ]
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "pods": [
                    {
                        "pod_id": view.pod_id,
                        "pod_name": view.pod_name,
                        "confirmed": view.confirmed,
                        "partial": view.partial,
                        "stale": view.stale,
                        "missing": view.missing,
                        "coverage": {
                            "replied": view.coverage.replied,
                            "inferred": view.coverage.inferred,
                            "no_data": view.coverage.no_data,
                            "waiting": view.coverage.waiting,
                            "no_data_causes": dict(view.coverage.causes),
                        },
                        "developers": [
                            {
                                "developer_id": developer.developer_id,
                                "developer_name": developer.developer_name,
                                "state": developer.state,
                                "source": developer.source.value,
                                "status_as_of": _iso(developer.status_as_of),
                                "summary": _clip(developer.summary),
                                **(
                                    {"evidence": [_clip(line) for line in developer.evidence]}
                                    if developer.evidence
                                    else {}
                                ),
                                **(
                                    {"no_data_cause": developer.no_data_cause}
                                    if developer.no_data_cause
                                    else {}
                                ),
                            }
                            for developer in view.developers[:25]
                        ],
                    }
                    for view in views
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class PodBlockersTool:
    tenant_id: str
    service: PersonaViewService
    repository: GraphRepository
    as_of: date

    name: str = "pod_blockers"
    description: str = (
        "Blockers open today in a pod, oldest first: what is blocked, for how many "
        "days, whose blocker it is and which work item it is attributed to. Omit "
        "pod_id to cover every pod. A blocker counts in the pod its blocked work sits "
        "with; one on a task a pod lists that counts in another pod comes with "
        "counted_in_this_pod false and the pods it counts in. Over several pods each "
        "blocker is listed once."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "pod_id": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of blockers to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        pods = await _pods(self.repository, self.tenant_id, arguments.get("pod_id"))
        if isinstance(pods, str):
            return json.dumps({"error": pods})
        limit = _bounded_int(arguments.get("limit"), default=20, maximum=50)
        views = [
            await self.service.pod_blockers(self.tenant_id, pod.id, self.as_of) for pod in pods
        ]
        blockers = [
            (view.pod_id, view.pod_name, blocker)
            for view in views
            for blocker in view.blockers
            if blocker.counted_here
        ]
        # A pod lists a blocker on a task of its own that counts in another pod
        # (PersonaViewService.pod_blockers). Over several pods it stays where it
        # counts, so a project's blockers are never counted twice.
        counted = {blocker.blocker_id for _pod_id, _pod_name, blocker in blockers}
        for view in views:
            for blocker in view.blockers:
                if not blocker.counted_here and blocker.blocker_id not in counted:
                    counted.add(blocker.blocker_id)
                    blockers.append((view.pod_id, view.pod_name, blocker))
        blockers.sort(key=lambda item: -item[2].age_days)
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "blocker_count": len(blockers),
                "blockers": [
                    {
                        "pod_id": pod_id,
                        "pod_name": pod_name,
                        "blocker_id": blocker.blocker_id,
                        "description": _clip(blocker.description),
                        "age_days": blocker.age_days,
                        "first_seen_on": blocker.first_seen_on.isoformat(),
                        "owner_id": blocker.owner_id,
                        "owner_name": blocker.owner_name,
                        "work_item_id": (
                            blocker.work_item_ref.id if blocker.work_item_ref else None
                        ),
                        "unattributed": blocker.unattributed,
                        "counted_in_this_pod": blocker.counted_here,
                        **(
                            {}
                            if blocker.counted_here
                            else {"counts_in": [pod.name for pod in blocker.counted_in]}
                        ),
                    }
                    for pod_id, pod_name, blocker in blockers[:limit]
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class DeliveryForecastTool:
    """Whether a project or pod will make its committed date, as Delivery forecasts it.

    Asked whether a project would make its date, Ask had no forecast to read:
    it said it could not tell, or called a red project late from its colour
    alone. This hands over the committed date, history's 50% and 85% dates,
    the verdict and its reasons from ForecastService.project_delivery, the read
    behind /projects/{id}/delivery and /pods/{id}/delivery, after the check
    each of those routes makes through DeliveryScopeService: a scrum master
    reads the projects their pods work on. A read the route would refuse comes
    back in the route's own words, never as data. The route also answers a
    developer for their own pod, but Ask is not for developers: AskService
    offers no tool to a role /ask refuses (may_ask), so they never get here.
    """

    principal: Principal
    service: ForecastService
    scope: DeliveryScopeService
    repository: GraphRepository
    as_of: date

    name: str = "delivery_forecast"
    description: str = (
        "Whether a project or pod will make its committed delivery date, on one day, "
        "today unless as_of names an earlier one: the committed date and how often it "
        "moved, history's 50% and 85% likely finish dates (forecast_p50, forecast_p85), "
        "the verdict against the date and what it rests on, whether there is enough "
        "history to forecast, the team's own date (the latest ETA or due date the team gave), "
        "the open requirements "
        "with no ETA or due date (open_without_date, and their keys in "
        "open_without_date_keys), and the reasons in "
        "Delivery's own words. A project comes with its pods' and releases' verdicts, a "
        "pod with its part of each project it works on, a program with each of its "
        "projects; leave node_id unset to cover every project. Use it for 'will X make "
        "its date', 'is X on track for its date' and 'which projects will miss their "
        "date'; search_graph_nodes turns a name into the node_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The project, pod or program to forecast. Leave unset "
                    "for every project.",
                },
                "as_of": _AS_OF_PARAMETER,
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        node_id = _string_argument(arguments.get("node_id"))
        try:
            if node_id is None:
                payload = await self._every_project(as_of)
            else:
                payload = await self._node(node_id, as_of)
        except AuthorizationDenied as exc:
            payload = {"error": str(exc)}
        if "error" in payload:
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"as_of": as_of.isoformat(), **payload}, ensure_ascii=False)

    async def _node(self, node_id: str, as_of: date) -> dict[str, object]:
        node = await self.repository.get_node(self.principal.tenant_id, node_id, as_of=as_of)
        if node is None:
            raise GraphNotFound(
                f"{node_id} not found; search_graph_nodes finds a project, pod or program by name"
            )
        if node.kind is NodeKind.PROJECT:
            await self.scope.ensure_project_read(self.principal, node.id)
            return {"projects": [await self._project(node.id, as_of, with_parts=True)]}
        if node.kind is NodeKind.POD:
            await self.scope.ensure_pod_dates(self.principal, node.id)
            return await self._pod(node, as_of)
        if node.kind is NodeKind.PROGRAM:
            return await self._program(node, as_of)
        return {
            "error": f"{node_id} is a {node.kind.value}; delivery_forecast covers a project, "
            "pod or program"
        }

    async def _project(
        self, project_id: str, as_of: date, *, with_parts: bool
    ) -> dict[str, object]:
        view = await self.service.project_delivery(self.principal.tenant_id, project_id, as_of)
        payload: dict[str, object] = {
            "project_id": project_id,
            "project_name": view.project.name,
            **_forecast_payload(view.project),
        }
        if with_parts:
            payload["pods"] = [
                {"pod_id": pod.scope.id, "pod_name": pod.name, **_forecast_summary(pod)}
                for pod in view.pods[:_MAX_PODS]
            ]
            payload["releases"] = [
                {"release_name": release.name, **_forecast_summary(release)}
                for release in view.releases[:_MAX_PARTS]
            ]
        return payload

    async def _pod(self, pod: GraphNode, as_of: date) -> dict[str, object]:
        """The pod's part of each project it works on, as /pods/{id}/delivery lists them."""
        tenant_id = self.principal.tenant_id
        projects: list[dict[str, object]] = []
        for project in await self.service.pod_projects(tenant_id, pod.id, as_of):
            view = await self.service.project_delivery(tenant_id, project.id, as_of)
            part = next((item for item in view.pods if item.scope.id == pod.id), None)
            if part is None:
                continue
            projects.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "project_target_date": _iso(view.project.target),
                    **_forecast_payload(part),
                }
            )
        return {"pod_id": pod.id, "pod_name": pod.name, "projects": projects}

    async def _program(self, program: GraphNode, as_of: date) -> dict[str, object]:
        tenant_id = self.principal.tenant_id
        contained = {
            edge.to_node_id
            for edge in await self.repository.list_edges(
                tenant_id, from_node_id=program.id, kind=EdgeKind.CONTAINS
            )
            if edge.is_active_on(as_of)
        }
        projects = [
            node
            for node in await self.repository.list_nodes(tenant_id, NodeKind.PROJECT, as_of=as_of)
            if node.id in contained
        ]
        payload = await self._projects(projects, as_of)
        return {"program_id": program.id, "program_name": program.name, **payload}

    async def _every_project(self, as_of: date) -> dict[str, object]:
        nodes = await self.repository.list_nodes(
            self.principal.tenant_id, NodeKind.PROJECT, as_of=as_of
        )
        return await self._projects(nodes, as_of)

    async def _projects(self, nodes: Sequence[GraphNode], as_of: date) -> dict[str, object]:
        """The projects among ``nodes`` the asker reads, by name, saying what was left out."""
        readable = set(await self.scope.readable_projects(self.principal, (n.id for n in nodes)))
        listed = sorted(
            (node for node in nodes if node.id in readable),
            key=lambda node: (node.name.casefold(), node.id),
        )
        payload: dict[str, object] = {
            "projects": [
                await self._project(node.id, as_of, with_parts=False)
                for node in listed[:_MAX_PROJECTS]
            ]
        }
        unread = len(nodes) - len(listed)
        if unread:
            payload["not_shown"] = (
                f"{_count_words(unread, 'project')} outside what the asker reads "
                f"{'is' if unread == 1 else 'are'} left out."
            )
        if len(listed) > _MAX_PROJECTS:
            payload["truncated"] = (
                f"Only the first {_MAX_PROJECTS} of {len(listed)} projects by name are listed."
            )
        return payload


@dataclass(frozen=True, kw_only=True)
class DeliveryImpactTool:
    """What moved a project's, release's or pod's date, and why, as Delivery takes it apart.

    Asked what delayed a project or what changed its timeline, Ask could only
    say the forecast's dates and verdict. This hands over the forecast's move
    over a period, taken apart (DeliveryImpactService over forecast_days):
    each driver in working days with the work items behind it -- pace, scope
    added by kind (change requests, without the change label, bugs, moved in),
    scope removed, reopened, re-estimated, throughput -- the committed date's
    moves with who and why, the team's own date, the late and undated
    requirements as evidence, and the timeline's markers with the link to its
    row. The gate is the delivery forecast's own (DeliveryImpactService.access,
    the rule behind GET /projects/{id}/delivery/impact): a read the route would
    refuse comes back in its words, never as data; executives get no "added
    by". Ask is not for developers (may_ask), so they never get here.
    """

    principal: Principal
    service: DeliveryImpactService
    forecasts: ForecastService
    repository: GraphRepository
    as_of: date

    name: str = "delivery_impact"
    description: str = (
        "Why a project's, release's or pod's delivery date moved over a period, and what "
        "impacted its timeline: a summary whose first line says what is not known and "
        "whose next says where the likely date stands and why, "
        "the likely date then and now, how many working days it moved, and at most three "
        "causes with their working days and the requirements behind them -- scope added "
        "(change requests carrying the change label, additions without it, bugs, moved "
        "in, reopened), scope removed, and team speed (how fast the team finishes) -- plus "
        "each move of the committed date with who and why, the team's own date and what "
        "moved it, requirements open past their date and those with no date, and the "
        "timeline's markers. Use it for 'why did X's date "
        "move', 'what changed or delayed X since planning', 'what scope was added' and "
        "'what impacted X's timeline'; search_graph_nodes turns a name into the node_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The project or pod.",
                },
                "release_id": {
                    "type": "string",
                    "description": "One release of the project, from a project's releases.",
                },
                "period": {
                    "type": "string",
                    "enum": [kind.value for kind in PeriodKind if kind is not PeriodKind.FROM_DAY],
                    "description": "The period the move covers: since planning was marked "
                    "complete (the default), the last 7 or 30 days, or since the committed "
                    "date last changed. Give from instead for a period from a named day.",
                },
                "from": {
                    "type": "string",
                    "format": "date",
                    "description": "The period's first day, when it starts on a named day.",
                },
                "measure": {
                    "type": "string",
                    "enum": [measure.value for measure in Measure],
                    "description": "The 85% date (p85, the default, as the verdict reads it) "
                    "or the 50% date (p50).",
                },
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["node_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        node_id = _required_string(arguments.get("node_id"), "node_id")
        release_id = _string_argument(arguments.get("release_id"))
        period = _impact_period(arguments)
        measure = _measure(arguments.get("measure"))
        tenant_id = self.principal.tenant_id
        node = await self.repository.get_node(tenant_id, node_id, as_of=as_of)
        if node is None:
            raise GraphNotFound(
                f"{node_id} not found; search_graph_nodes finds a project or pod by name"
            )
        try:
            if node.kind is NodeKind.PROJECT:
                scope = (
                    CommitmentScope(
                        kind=CommitmentScopeKind.RELEASE, id=release_id, project_id=node.id
                    )
                    if release_id
                    else CommitmentScope(
                        kind=CommitmentScopeKind.PROJECT, id=node.id, project_id=node.id
                    )
                )
                payload = await self._scope(scope, period, measure, as_of)
            elif node.kind is NodeKind.POD:
                payload = {
                    "pod_id": node.id,
                    "pod_name": node.name,
                    "projects": [
                        await self._scope(
                            CommitmentScope(
                                kind=CommitmentScopeKind.POD, id=node.id, project_id=project.id
                            ),
                            period,
                            measure,
                            as_of,
                        )
                        for project in (
                            await self.forecasts.pod_projects(tenant_id, node.id, as_of)
                        )[:_MAX_IMPACT_PROJECTS]
                    ],
                }
            else:
                payload = {
                    "error": f"{node_id} is a {node.kind.value}; delivery_impact covers a "
                    "project, one of its releases, or a pod"
                }
        except AuthorizationDenied as exc:
            payload = {"error": str(exc)}
        if "error" in payload:
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"as_of": as_of.isoformat(), **payload}, ensure_ascii=False)

    async def _scope(
        self, scope: CommitmentScope, period: ImpactPeriod, measure: Measure, as_of: date
    ) -> dict[str, object]:
        access = await self.service.access(self.principal, scope)
        view = await self.service.impact(
            self.principal.tenant_id,
            scope,
            period=period,
            measure=measure,
            as_of=as_of,
            access=access,
        )
        return _impact_payload(view)


@dataclass(frozen=True, kw_only=True)
class ReleaseReadinessTool:
    """What a project, one of its releases, or a pod still needs before production.

    Asked what was missing before a project could go to production, Ask had no
    readiness to read: it explained the project's colour and never named the
    blocking criterion the readiness board showed missing. This hands over that
    board -- each criterion, whether it is covered, missing or unsure, the
    evidence and candidate issue keys, when it is needed by, and the drafted
    issue's status -- from ReleaseReadinessService.project_board or pod_board,
    the reads behind /projects/{id}/readiness and /pods/{id}/readiness, after the
    same check those routes make (readiness_access). A read the route would
    refuse comes back in the route's own words, never as data; an executive
    gets the rows without drafts, as the route gives them. It writes nothing.
    Ask is not for developers (may_ask), so they never get here.
    """

    principal: Principal
    service: ReleaseReadinessService
    forecasts: ForecastService
    repository: GraphRepository
    #: The day asked about. Readiness keeps no history, so a board checked after
    #: it says so rather than passing for that day's.
    as_of: date

    name: str = "release_readiness"
    description: str = (
        "Whether a project, one of its releases, or a pod is ready for production, as "
        "Release readiness checks it: each release criterion (its name, blocking or "
        "advisory, the stage it is needed before), whether it is covered, missing, unsure "
        "or not applicable and why, the evidence issue keys and how each was found, what "
        "is still needed by when (what_is_needed, needed_by), any requirement that already "
        "reached the stage without it (reached_stage_without_it), and the status of the "
        "issue drafted for a missing one. A "
        "project comes with each of its releases' counts; give release_id for one "
        "release's criteria. Use it for 'is X ready for production', 'what is missing "
        "before X can go live', 'can we release' and 'what must happen before the "
        "release'; search_graph_nodes turns a name into the node_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The project or pod to check.",
                },
                "release_id": {
                    "type": "string",
                    "description": "One release of the project, from a project's releases.",
                },
            },
            "required": ["node_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        node_id = _required_string(arguments.get("node_id"), "node_id")
        release_id = _string_argument(arguments.get("release_id"))
        tenant_id = self.principal.tenant_id
        node = await self.repository.get_node(tenant_id, node_id)
        if node is None:
            raise GraphNotFound(
                f"{node_id} not found; search_graph_nodes finds a project or pod by name"
            )
        try:
            if node.kind is NodeKind.PROJECT:
                viewer = await project_viewer(
                    self.principal, self.forecasts, self.service, node.id, reading=True
                )
                release = await self._release(node.id, release_id) if release_id else None
                board = await self.service.project_board(
                    tenant_id, node.id, viewer, release=release
                )
            elif node.kind is NodeKind.POD:
                viewer = await pod_viewer(self.principal, self.forecasts, node.id)
                board = await self.service.pod_board(tenant_id, node.id, viewer)
            else:
                return json.dumps(
                    {
                        "error": f"{node_id} is a {node.kind.value}; release_readiness covers "
                        "a project, one of its releases, or a pod"
                    }
                )
        except AuthorizationDenied as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps(_readiness_payload(board, self.as_of), ensure_ascii=False)

    async def _release(self, project_id: str, release_id: str) -> Release:
        """The project's release, as the route finds it: another project's is no release."""
        release = await self.forecasts.release(self.principal.tenant_id, release_id)
        if release.project_id != project_id:
            raise GraphNotFound(f"{release_id} is no release of {project_id}")
        return release


@dataclass(frozen=True, kw_only=True)
class TeamProfilesTool:
    """Who brings what to each pod: every member's role in it and their profile there.

    For a project it also names the profiles its switched-on release criteria
    ask for (``owner_profile``) that none of its pods holds, so "who does the
    security review for X" can say nobody does yet. Names are directory data;
    the read is behind Ask's own check (``may_ask``). It writes nothing, and a
    profile is never a rating: no counts per person, no ranking.
    """

    tenant_id: str
    service: ProfileService
    repository: GraphRepository
    readiness: ReleaseReadinessService | None
    as_of: date

    name: str = "team_profiles"
    description: str = (
        "Who brings what to each pod: every member with their role in the pod (developer, "
        "scrum master, product owner, manager) and their profile there (development, QA and "
        "test, security, product, design, TPM / delivery, ops, ...), whether someone chose it "
        "(checked) or it only follows the pod role, and for a project the profiles its release "
        "criteria ask for that none of its pods holds (missing). Give pod_id or project_id, "
        "and profile to keep one (a profile id or its label, such as security or QA). Use it "
        "for 'who is the security contact for X', 'who tests in Y', 'is there a QA in Z'."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "pod_id": {"type": "string"},
                "project_id": {"type": "string"},
                "profile": {
                    "type": "string",
                    "description": "One profile, by id or label, such as security or QA.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        project_id = _string_argument(arguments.get("project_id"))
        wanted = _string_argument(arguments.get("profile"))
        catalog = await self.service.catalog(self.tenant_id)
        slug = _profile_slug(wanted, catalog) if wanted else None
        if wanted and slug is None:
            return json.dumps(
                {
                    "error": f"no profile {wanted!r}; the profiles are "
                    + ", ".join(f"{entry.slug} ({entry.label})" for entry in catalog)
                },
                ensure_ascii=False,
            )
        project: GraphNode | None = None
        if project_id is not None:
            project = await self.repository.get_node(self.tenant_id, project_id, as_of=self.as_of)
            if project is None or project.kind is not NodeKind.PROJECT:
                return json.dumps(
                    {
                        "error": f"project {project_id} not found; search_graph_nodes with "
                        "kinds ['project'] finds its id"
                    }
                )
            pods: Sequence[GraphNode] | str = await _project_pods(
                self.repository, self.tenant_id, project.id, self.as_of
            )
        else:
            pods = await _pods(self.repository, self.tenant_id, arguments.get("pod_id"))
        if isinstance(pods, str):
            return json.dumps({"error": pods})
        held: set[str] = set()
        rows: list[dict[str, object]] = []
        for pod in pods:
            view = await self.service.pod_profiles(self.tenant_id, pod.id, self.as_of)
            held.update(member.profile for member in view.members)
            rows.append(
                {
                    "pod_id": view.pod_id,
                    "pod_name": view.pod_name,
                    "members": [
                        {
                            "name": member.member_name,
                            "role_in_pod": member.role,
                            "profile": member.profile,
                            "profile_label": member.label,
                            "checked": member.checked,
                        }
                        for member in view.members
                        if slug is None or member.profile == slug
                    ][:40],
                }
            )
        payload: dict[str, object] = {"as_of": self.as_of.isoformat(), "pods": rows}
        if project is not None:
            payload["project_name"] = project.name
            asked = await self.readiness.asked_profiles(self.tenant_id) if self.readiness else {}
            labels = {entry.slug: entry.label for entry in catalog}
            payload["missing"] = [
                {
                    "profile": missing,
                    "profile_label": labels.get(missing, missing),
                    "asked_by_criteria": list(criteria),
                }
                for missing, criteria in sorted(asked.items())
                if missing not in held and (slug is None or missing == slug)
            ]
        return json.dumps(payload, ensure_ascii=False)


def _profile_slug(wanted: str, catalog: Sequence[ProfileDefinition]) -> str | None:
    """A profile asked for by id or label ("QA", "qa and test"), or None when none matches."""
    folded = " ".join(wanted.split()).casefold()
    for entry in catalog:
        if folded in {entry.slug.casefold(), entry.label.casefold()}:
            return entry.slug
    for entry in catalog:
        if folded and (folded in entry.label.casefold() or folded in entry.title_words):
            return entry.slug
    return None


async def _project_pods(
    repository: GraphRepository, tenant_id: str, project_id: str, as_of: date
) -> Sequence[GraphNode]:
    pods: list[GraphNode] = []
    for edge in await repository.list_edges(
        tenant_id, from_node_id=project_id, kind=EdgeKind.CONTAINS
    ):
        if not edge.is_active_on(as_of):
            continue
        node = await repository.get_node(tenant_id, edge.to_node_id, as_of=as_of)
        if node is not None and node.kind is NodeKind.POD:
            pods.append(node)
    return sorted(pods, key=lambda pod: (pod.name.casefold(), pod.id))[:_MAX_PODS]


@dataclass(frozen=True, kw_only=True)
class RecordedTool:
    """Runs a tool and notes that it ran.

    ``tools_used`` used to be whatever the model wrote into its JSON, so an
    answer could claim tools it never called -- or report none when it had.
    This records the calls that actually happened. A missing id or a bad
    argument comes back to the model as an error it can correct rather than
    failing the whole question.
    """

    inner: AgentTool
    calls: list[str]

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def description(self) -> str:
        return self.inner.description

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.inner.parameters

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        self.calls.append(self.inner.name)
        try:
            return await self.inner.run(arguments)
        except (GraphNotFound, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class AnswerReader:
    """A reply made readable against one day's nodes.

    ``view`` takes a whole reply apart into the answer, its references and
    their sources; ``readable`` gives any other model-written text the same
    clean-up -- one bullet marker, no raw ids -- before a reader sees it.
    """

    nodes: Mapping[str, GraphNode]
    labelled: Mapping[str, GraphNode]

    def view(self, text: str, *, trace_id: str, tools_used: Iterable[str]) -> AskResponseView:
        parsed = _parse_answer(text)
        answer = self.readable(parsed.answer) or _NO_ANSWER
        references = _references(parsed, self.nodes, self.labelled) or _named_in(
            answer, self.labelled
        )
        return AskResponseView(
            answer=answer,
            references=references,
            tools_used=tuple(dict.fromkeys(tools_used)),
            trace_id=trace_id,
            sources=tuple(_source(reference, self.nodes) for reference in references),
            follow_ups=self._questions(parsed.follow_ups),
        )

    def _questions(self, follow_ups: Iterable[str]) -> tuple[str, ...]:
        """Follow-ups made readable: no raw ids, one line each, short, at most three."""
        readable = (
            " ".join(self.readable(question).split())[:_MAX_FOLLOW_UP_CHARS]
            for question in follow_ups
        )
        return tuple(dict.fromkeys(q for q in readable if q))[:MAX_FOLLOW_UPS]

    def readable(self, text: str) -> str:
        return _without_raw_ids(_tidy_lines(text), self.nodes)

    def cut_short(
        self, view: AskResponseView, *, read: Iterable[str], unrun: Sequence[LlmToolCall]
    ) -> AskResponseView:
        """An answer written when the tool rounds ran out, held to what was read.

        A claim that something is absent goes unless a read of its kind ran and
        none was left unrun (``without_unread_negatives``), and the answer opens
        by saying what it could not check, unless it already says so.
        """
        answer = without_unread_negatives(
            view.answer, read=frozenset(read), pending=frozenset(call.name for call in unrun)
        )
        if not said_it_ran_out(answer):
            answer = "\n".join(line for line in (self.ran_out(unrun), answer) if line)
        return replace(view, answer=answer)

    def ran_out(self, unrun: Sequence[LlmToolCall]) -> str:
        """'I ran out of steps before checking the open blockers of Payments Pod.'"""
        checks = list(dict.fromkeys(self._unchecked(call) for call in unrun))
        if not checks:
            return ""
        listed = checks[0] if len(checks) == 1 else f"{', '.join(checks[:-1])} and {checks[-1]}"
        return self.readable(f"I ran out of steps before checking {listed}.")

    def _unchecked(self, call: LlmToolCall) -> str:
        noun = _READ_WORDS.get(call.name, call.name.replace("_", " "))
        for key in _NODE_ARGUMENTS:
            value = call.arguments.get(key)
            if isinstance(value, str) and value.strip():
                node = self.nodes.get(value.strip())
                label = node_label(node) if node is not None else None
                # Never a raw id: a node it cannot name is left unnamed.
                return f"{noun} of {label}" if label else noun
        query = call.arguments.get("query")
        if call.name == "search_graph_nodes" and isinstance(query, str) and query.strip():
            return f"{noun} for {' '.join(query.split())[:_MAX_LOOKUP_CHARS]}"
        if call.name in _EVERY_POD_READS:
            return f"{noun} of every pod"
        return noun


def said_it_ran_out(answer: str) -> bool:
    """Whether an answer says it could not check everything."""
    return _RAN_OUT.search(answer) is not None


def without_unread_negatives(
    answer: str, *, read: Collection[str], pending: Collection[str] = ()
) -> str:
    """``answer`` without a claim that something is absent which no finished read shows.

    For an answer written after its reads were cut short. A claim of no
    blockers, no risks or nothing missing stays only when a tool that reads
    that kind ran (``read``) and none was left unrun (``pending``); otherwise
    its sentence goes, and a line left empty with it.
    """
    unread = [
        pattern
        for tools, pattern in _UNREAD_NEGATIVES
        if not tools & frozenset(read) or tools & frozenset(pending)
    ]
    if not unread:
        return answer
    kept: list[str] = []
    for line in answer.splitlines():
        bullet = _BULLET.match(line)
        body = line[bullet.end() :] if bullet else line
        text = " ".join(
            sentence
            for sentence in _SENTENCE_END.split(body.strip())
            if not any(pattern.search(sentence) for pattern in unread)
        ).strip()
        if text:
            kept.append(f"• {text}" if bullet else text)
    return "\n".join(kept)


def remembered(view: AskResponseView, context: AskContext) -> AskResponseView:
    """The view, with the new summary when the conversation was compacted for it."""
    if not context.summarized:
        return view
    return replace(view, summary=context.summary, summarized_turns=context.summarized)


def may_ask(principal: Principal) -> bool:
    """Whether /ask admits this role: the team-or-exec aggregate read.

    The route and AskService.tools_for both decide with this one rule. A role it
    refuses, a developer, is offered no tool at all, even where a tool's REST
    twin would answer them for their own pod or project.
    """
    policy = AuthorizationPolicy()
    return policy.can(principal, Capability.READ_TEAM_AGGREGATE) or policy.can(
        principal, Capability.READ_EXEC_AGGREGATE
    )


def _reads_delivery(principal: Principal) -> bool:
    """Whether /projects/{id}/delivery or /pods/{id}/delivery answers the principal for
    some project or pod: everywhere by role, or within their own part of the tree."""
    policy = AuthorizationPolicy()
    return reads_project(policy, principal, own=True) or reads_pod_dates(
        policy, principal, own=True
    )


def _reads_impact(principal: Principal) -> bool:
    """Whether /projects/{id}/delivery/impact answers the principal somewhere: the
    project's readers everywhere, and a scrum master for their own projects and pods."""
    return reads_project(AuthorizationPolicy(), principal, own=True)


def _has(capability: Capability) -> Callable[[Principal], bool]:
    return lambda principal: AuthorizationPolicy().can(principal, capability)


def _reason_kinds(principal: Principal) -> frozenset[NodeKind]:
    """The node kinds whose reasons this principal's REST routes would serve."""
    policy = AuthorizationPolicy()
    return frozenset(
        kind
        for kind, capabilities in _REASON_CAPABILITIES.items()
        if all(policy.can(principal, capability) for capability in capabilities)
    )


class AskService:
    def __init__(
        self,
        llm_provider: LlmProvider,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        flow_metrics_service: FlowMetricsService,
        persona_view_service: PersonaViewService,
        risk_service: RiskService,
        forecast_service: ForecastService,
        delivery_scope_service: DeliveryScopeService,
        release_readiness_service: ReleaseReadinessService,
        model: str,
        impact_service: DeliveryImpactService | None = None,
        tool_agent: ToolCallingAgent | None = None,
        max_tool_iterations: int = ASK_MAX_TOOL_ITERATIONS,
        profile_service: ProfileService | None = None,
        hygiene_service: HygieneService | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._llm_provider = llm_provider
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._flow_metrics_service = flow_metrics_service
        self._persona_view_service = persona_view_service
        self._risk_service = risk_service
        self._forecast_service = forecast_service
        self._delivery_scope_service = delivery_scope_service
        self._release_readiness_service = release_readiness_service
        self._profile_service = profile_service
        self._impact_service = impact_service
        self._hygiene_service = hygiene_service
        self._model = model
        self._tool_agent = tool_agent or ToolCallingAgent(
            llm_provider=llm_provider,
            max_tool_iterations=max_tool_iterations,
            cap_notice=ASK_CAP_NOTICE,
        )
        # The day a question with no as_of is asked for; the API passes the
        # tenant's today, the day the graph links Ask reads are dated on.
        self._today = today

    def today(self) -> date:
        """The day a question with no ``as_of`` is answered for."""
        return self._today()

    async def ask(
        self,
        *,
        principal: Principal,
        question: str,
        correlation_id: str,
        as_of: date | None = None,
        conversation: AskConversation | None = None,
    ) -> AskResponseView:
        asked_for = as_of or self._today()
        context = await context_for(
            conversation,
            llm=self._llm_provider,
            model=self._model,
            tenant_id=principal.tenant_id,
            correlation_id=correlation_id,
        )
        request = LlmRequest(
            tenant_id=principal.tenant_id,
            prompt=_prompt(question, asked_for),
            model=self._model,
            correlation_id=correlation_id,
            system=ASK_SYSTEM_PROMPT,
            messages=(*context.messages(), LlmMessage(role="user", content=question)),
            metadata={
                "agent": "ask_service",
                "purpose": "graph_question",
                "as_of": asked_for.isoformat(),
            },
        )
        calls: list[str] = []
        tools = tuple(
            RecordedTool(inner=tool, calls=calls) for tool in self.tools_for(principal, asked_for)
        )
        result = await self._tool_agent.run_loop(request, tools)
        reader = await self.reader(
            principal.tenant_id, asked_for, people=not hides_names(principal)
        )
        response = result.response
        view = reader.view(response.text, trace_id=response.trace_id, tools_used=calls)
        if result.cap_reached:
            view = reader.cut_short(view, read=calls, unrun=result.unrun)
        return remembered(view, context)

    async def reader(self, tenant_id: str, as_of: date, *, people: bool = True) -> AnswerReader:
        """What turns a model's reply into an answer for the nodes of ``as_of``.

        ``people`` False is a reader who reads no person's name (an executive,
        D103): a person's id in an answer, or among its sources, reads as their
        role or team, and no name in the answer is taken for a person.
        """
        nodes = await _nodes_by_any_id(self._graph_repository, tenant_id, as_of)
        if people:
            return AnswerReader(nodes=nodes, labelled=_nodes_by_label(nodes))
        words = await AskPeopleWords(
            repository=self._graph_repository, tenant_id=tenant_id, as_of=as_of
        ).get()
        said = {
            key: replace(node, name=words.label(node.id))
            if node.kind is NodeKind.DEVELOPER
            else node
            for key, node in nodes.items()
        }
        teams = {key: node for key, node in said.items() if node.kind is not NodeKind.DEVELOPER}
        return AnswerReader(nodes=said, labelled=_nodes_by_label(teams))

    def tools_for(self, principal: Principal, as_of: date) -> tuple[AgentTool, ...]:
        """The tools this principal may use, each gated as its REST twin is.

        Ask must never read more than the asker could fetch directly, so a tool
        is offered only when the endpoint serving the same data would answer
        them. A tool left out is simply absent: the model cannot see it, and a
        call to it by name comes back as not available.

        A role /ask itself refuses is offered nothing, whatever a tool's REST
        twin would say for them: Ask is not for developers.
        """
        if not may_ask(principal):
            return ()
        tenant_id = principal.tenant_id
        graph = self._graph_repository
        personas = self._persona_view_service
        offered: tuple[tuple[AgentTool, Callable[[Principal], bool]], ...] = (
            # Graph reads behind /ask's own aggregate check.
            (
                SearchGraphNodesTool(tenant_id=tenant_id, repository=graph, as_of=as_of),
                may_ask,
            ),
            (
                GraphNeighborsTool(tenant_id=tenant_id, repository=graph, as_of=as_of),
                may_ask,
            ),
            # /portfolio/feed
            (
                RecentFactsTool(
                    tenant_id=tenant_id, repository=self._time_series_repository, as_of=as_of
                ),
                may_ask,
            ),
            # /workstreams/{id}/flow and /portfolio/flow
            (
                WorkstreamFlowTool(
                    tenant_id=tenant_id, service=self._flow_metrics_service, as_of=as_of
                ),
                may_ask,
            ),
            (
                PortfolioFlowTool(
                    tenant_id=tenant_id, service=self._flow_metrics_service, as_of=as_of
                ),
                may_ask,
            ),
            # /portfolio/risks and /projects/{id}/risks
            (
                OpenRisksTool(
                    tenant_id=tenant_id,
                    service=self._risk_service,
                    repository=graph,
                    as_of=as_of,
                ),
                may_ask,
            ),
            # /workstreams/{id}/progress
            (
                WorkstreamProgressTool(tenant_id=tenant_id, service=personas, as_of=as_of),
                _has(Capability.READ_PROJECT_PROGRESS),
            ),
            # /programs/{id}/tree, /projects|workstreams/{id}/progress, /pods/{id}/rollup:
            # offered when any of them answers, and per kind only where it does.
            (
                StatusReasonsTool(
                    tenant_id=tenant_id,
                    service=personas,
                    risks=self._risk_service,
                    repository=graph,
                    as_of=as_of,
                    kinds=_reason_kinds(principal),
                    counts_only=not reads_people(principal),
                ),
                lambda asker: bool(_reason_kinds(asker)),
            ),
            # /portfolio/heatmap
            (
                PortfolioHeatmapTool(
                    tenant_id=tenant_id,
                    service=personas,
                    repository=graph,
                    as_of=as_of,
                    counts_only=not reads_people(principal),
                    asker_id=principal.subject,
                ),
                _has(Capability.READ_PORTFOLIO_HEATMAP),
            ),
            # /pods/{id}/checkins
            (
                PodCheckinsTool(
                    tenant_id=tenant_id, service=personas, repository=graph, as_of=as_of
                ),
                _has(Capability.READ_POD_CHECKINS),
            ),
            # /pods/{id}/blockers
            (
                PodBlockersTool(
                    tenant_id=tenant_id, service=personas, repository=graph, as_of=as_of
                ),
                _has(Capability.READ_POD_BLOCKERS),
            ),
            # /projects/{id}/delivery and /pods/{id}/delivery: offered when either
            # answers somewhere, and per project or pod only where it does. A
            # developer's own pod answers, but they never reach here: may_ask.
            (
                DeliveryForecastTool(
                    principal=principal,
                    service=self._forecast_service,
                    scope=self._delivery_scope_service,
                    repository=graph,
                    as_of=as_of,
                ),
                _reads_delivery,
            ),
            # /projects/{id}/readiness and /pods/{id}/readiness: offered when a
            # board answers the role somewhere, and per project or pod only
            # where it does (readiness_access), as delivery_forecast is.
            (
                ReleaseReadinessTool(
                    principal=principal,
                    service=self._release_readiness_service,
                    forecasts=self._forecast_service,
                    repository=graph,
                    as_of=as_of,
                ),
                reads_some_board,
            ),
        )
        if self._profile_service is not None and not hides_names(principal):
            # Names and pod roles are directory data, which every role reads but an
            # executive (no People & roles for them, D87): behind /ask's own check, as
            # the graph reads are.
            offered = (
                *offered,
                (
                    TeamProfilesTool(
                        tenant_id=tenant_id,
                        service=self._profile_service,
                        repository=graph,
                        readiness=self._release_readiness_service,
                        as_of=as_of,
                    ),
                    may_ask,
                ),
            )
        if self._impact_service is not None:
            # /projects/{id}/delivery/impact: the delivery forecast's readers, per
            # project or pod as DeliveryImpactService.access decides.
            offered += (
                (
                    DeliveryImpactTool(
                        principal=principal,
                        service=self._impact_service,
                        forecasts=self._forecast_service,
                        repository=graph,
                        as_of=as_of,
                    ),
                    _reads_impact,
                ),
            )
        if self._hygiene_service is not None:
            # /projects/{id}/hygiene and /pods/{id}/hygiene: the readiness board's read
            # rule (readiness_access), per project or pod; an executive reads counts only.
            offered = (
                *offered,
                (
                    JiraHygieneTool(
                        principal=principal,
                        service=self._hygiene_service,
                        readiness=self._release_readiness_service,
                        forecasts=self._forecast_service,
                        repository=graph,
                        as_of=as_of,
                    ),
                    reads_some_board,
                ),
            )
        tools = tuple(tool for tool, allowed in offered if allowed(principal))
        if hides_names(principal):
            # An executive's answers say each person as the Project page does: their
            # role or team, never a name or an id that leads to one (D103).
            words = AskPeopleWords(repository=graph, tenant_id=tenant_id, as_of=as_of)
            tools = tuple(NamelessTool(inner=tool, words=words) for tool in tools)
        return tools


def fact_window(arguments: Mapping[str, JsonScalar], as_of: date) -> DateWindow:
    """The days a recent_facts call covers, never running past the as-of date."""
    period = _string_argument(arguments.get("period"))
    windows = period_windows(as_of)
    if period in windows:
        return windows[period]
    until = _optional_date(arguments.get("until"))
    end = min(until, as_of) if until is not None else as_of
    since = _optional_date(arguments.get("since"))
    start = since if since is not None else windows["last_7_days"].start
    start = min(max(start, end - timedelta(days=_MAX_FACT_WINDOW_DAYS - 1)), end)
    return DateWindow(start=start, end=end)


def _prompt(question: str, as_of: date) -> str:
    return (
        date_context(as_of) + "Answer in the required shape -- a verdict line, at most 4 '• ' "
        "bullets, 80 words at most -- naming people, issues and merge requests (MRs, never PRs) "
        "rather than ids, and "
        "list the id of every node the answer names in references. "
        "Questions to ask next go in follow_ups, never inside answer. "
        f"Question: {question}"
    )


def date_context(as_of: date) -> str:
    """The day asked for and the periods around it, and how the tools read them."""
    windows = period_windows(as_of)
    yesterday = windows["yesterday"].start
    this_week = windows["this_week"]
    last_week = windows["last_week"]
    last_7_days = windows["last_7_days"]
    return (
        f"Today is {_WEEKDAYS[as_of.weekday()]} {as_of.isoformat()}. "
        f"Yesterday was {yesterday.isoformat()}. This week runs from Monday "
        f"{this_week.start.isoformat()} to today, so 'since Monday' starts "
        f"{this_week.start.isoformat()}; last week ran {last_week.start.isoformat()} "
        f"to {last_week.end.isoformat()}; the last 7 days run from "
        f"{last_7_days.start.isoformat()} to today. "
        "Resolve any relative period against these dates, and never guess a date. "
        "Status and risk tools describe one day: for questions about now, this week "
        "or what is at risk, leave their as_of unset to get today's. For what "
        "happened over a period, give recent_facts a period, or since and until. "
        "A tool asked about the wrong period comes back empty, and empty is not "
        "the same as nothing being wrong. "
    )


def _parse_answer(text: str) -> ParsedAnswer:
    """Take a reply apart, whatever shape it came back in.

    A JSON object -- fenced, wrapped in prose, or with raw newlines in its
    strings -- gives the answer and its references; one that does not decode
    still gives its answer field; anything else is the answer as written. In
    each case a "References: [...]" line is cut out of the answer and its items
    kept in ``cited``, so the reader never sees the line and sources still get
    what it listed. A fenced or prose-wrapped object used to be shown whole,
    and a prose reply showed its references line. A follow-ups list written
    into the answer, or beside the object, is cut out the same way, and its
    questions are the follow-ups when the reply has no follow_ups field.
    """
    answer, references, beside = _reply_parts(text)
    answer, asked = _cut_follow_ups(answer)
    beside, asked_beside = _cut_follow_ups(beside)
    answer, cited = _clean_answer(answer)
    _, cited_beside = _clean_answer(beside)
    return ParsedAnswer(
        answer=answer,
        references=_unique(references),
        cited=_unique((*cited, *cited_beside)),
        follow_ups=_follow_ups(text) or _unique((*asked, *asked_beside)),
    )


def _cut_follow_ups(text: str) -> tuple[str, list[str]]:
    """``text`` without any follow-ups list written into it, and that list's questions.

    The list comes inline ('follow_ups: ["A?", "B?"]'), as a JSON array over
    several lines, YAML-ish (a bare "follow_ups:" with a "- A?" line per
    question), or at the end of a line. A heading in words counts only when
    every item under it is a question; otherwise its lines stay as they were.
    """
    lines = text.splitlines()
    kept: list[str] = []
    asked: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        heading = _FOLLOW_UPS_HEADING.match(line)
        if heading is None:
            trailing = _TRAILING_FOLLOW_UPS.search(line)
            if trailing is not None:
                asked.extend(_question_items(trailing.group("items")))
                line = line[: trailing.start()]
            kept.append(line)
            continue
        block = [line]
        listed = heading.group("items")
        questions: list[str] = []
        if not listed.strip():
            # A bare heading: the list is on the lines under it.
            while index < len(lines) and not lines[index].strip():
                block.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].strip().startswith("["):
                listed = lines[index]
                block.append(lines[index])
                index += 1
            else:
                while index < len(lines) and (entry := _LIST_ITEM.match(lines[index])):
                    questions.append(_question(entry.group("item")))
                    block.append(lines[index])
                    index += 1
        while _opens_list(listed) and index < len(lines):
            listed = f"{listed}\n{lines[index]}"
            block.append(lines[index])
            index += 1
        questions = [*_question_items(listed), *(item for item in questions if item)]
        if heading.group("field") or (questions and all(q.endswith("?") for q in questions)):
            asked.extend(questions)
        else:
            kept.extend(block)
    return "\n".join(kept), asked


def _question_items(text: str) -> list[str]:
    """The questions of a written list: a JSON array, quoted strings, or bare questions.

    A list cut off part way keeps the questions it finished.
    """
    listed = text.strip().rstrip(",.").strip()
    if not listed:
        return []
    if listed.startswith("["):
        try:
            decoded = _JSON.decode(listed)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            return [item.strip() for item in decoded if isinstance(item, str) and item.strip()]
    bare = listed.strip("[]")
    # Every item in double quotes, the last perhaps never closed.
    leftover = _QUOTED.sub("", bare).strip(" \t\n,")
    if listed.startswith(("[", '"')) and (
        not leftover or (leftover.startswith('"') and leftover.count('"') == 1)
    ):
        return [item for item in (_json_string(raw) for raw in _QUOTED.findall(bare)) if item]
    # Bare, or quoted the Python way: ['Why?', "Who's next?"].
    split = _AFTER_QUESTION if "?" in bare else _ITEM_SPLIT
    return [item for item in (_question(part) for part in split.split(bare)) if item]


def _question(item: str) -> str:
    """One written question without its quotes, marks or trailing comma."""
    text = item.strip().rstrip(",").strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return _json_string(text[1:-1])
    return text.strip(_ITEM_EDGES).strip()


def _follow_ups(text: str) -> tuple[str, ...]:
    """The follow_ups array of a reply, from its object or, when that is broken, its text."""
    found = _answer_object(text)
    if found is not None:
        items = _field(found[0], "follow_ups", "followups", "follow_up_questions")
        if isinstance(items, list):
            return _unique([item for item in items if isinstance(item, str)])
        return ()
    listed = _FOLLOW_UPS_FIELD.search(text)
    if listed is None:
        return ()
    return _unique([_json_string(item) for item in _QUOTED.findall(listed.group("items"))])


def _reply_parts(text: str) -> tuple[str, list[str], str]:
    """The answer text, the references array, and any prose beside the object."""
    found = _answer_object(text)
    if found is not None:
        reply, beside = found
        answer = _field_text(_field(reply, "answer"))
        references = _field_items(_field(reply, "references", "sources"))
        if answer:
            return answer, references, beside
        # An object of references only, after the answer in prose.
        return beside, references, ""
    salvaged = _ANSWER_FIELD.search(text) if "{" in text else None
    if salvaged is not None:
        listed = _REFERENCES_FIELD.search(text)
        return (
            "\n".join(
                (
                    _json_string(salvaged.group("text")),
                    *_loose_lines(text, salvaged.end()),
                )
            ),
            _reference_items(listed.group("items")) if listed is not None else [],
            "",
        )
    return text, [], ""


def _loose_lines(text: str, end: int) -> list[str]:
    """Bare strings right after the answer field's, each one more line of the answer.

    A long answer has come back as '{"answer": "Pod is red:", "• Blocker",
    "references": [...]}': its bullets keyless, so the object is not JSON and
    only the verdict was kept. ``end`` is where the answer's text stops.
    """
    lines: list[str] = []
    position = end
    while (found := _LOOSE_LINE.match(text, position)) is not None:
        lines.append(_json_string(found.group("text")))
        # The next line starts at this one's closing quote.
        position = found.end() - 1
    return lines


def _answer_object(text: str) -> tuple[Mapping[str, object], str] | None:
    """The first JSON object in a reply with an answer or references, and the rest.

    Strings may hold raw newlines (``strict=False``): models write them, and
    a strict decode dropped the whole object for one.
    """
    start = text.find("{")
    for _ in range(_MAX_OBJECT_STARTS):
        if start == -1:
            break
        try:
            decoded, end = _JSON.raw_decode(text, start)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, dict) and any(
                str(key).casefold() in {"answer", "references", "sources"} for key in decoded
            ):
                return decoded, f"{text[:start]}\n{text[end:]}"
        start = text.find("{", start + 1)
    return None


def _field(reply: Mapping[str, object], *names: str) -> object:
    folded = {str(key).casefold(): value for key, value in reply.items()}
    return next((folded[name] for name in names if name in folded), None)


def _field_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(line.strip() for line in value if isinstance(line, str) and line.strip())
    return ""


def _field_items(value: object) -> list[str]:
    """A references value as strings: ids, ``{"id": ...}`` objects, or one listed string."""
    if isinstance(value, str):
        return _reference_items(value)
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        item = entry.get("id") if isinstance(entry, Mapping) else entry
        if isinstance(item, str | int) and not isinstance(item, bool) and str(item).strip():
            items.append(str(item).strip())
    return items


def _json_string(raw: str) -> str:
    """A JSON string's body as text, even when the reply was cut off inside it."""
    try:
        decoded = _JSON.decode(f'"{raw}"')
    except json.JSONDecodeError:
        decoded = raw.rstrip("\\").replace("\\n", "\n").replace('\\"', '"')
    return decoded.strip() if isinstance(decoded, str) else ""


def _reference_items(text: str) -> list[str]:
    """The items of a written list: a JSON array, or entries split on commas."""
    listed = text.strip().rstrip(".").strip(_ITEM_EDGES)
    if listed.startswith("["):
        try:
            decoded = _JSON.decode(listed)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            return _field_items(decoded)
        listed = listed.strip("[]")
    items = (item.strip(_ITEM_EDGES).rstrip(".") for item in _ITEM_SPLIT.split(listed))
    return [item.strip(_ITEM_EDGES) for item in items if item.strip(_ITEM_EDGES)]


def _clean_answer(text: str) -> tuple[str, list[str]]:
    """The answer's own lines, and the items of every references line cut from it.

    A line that starts with References, Refs, Sources or Citations and a colon
    goes, with the list under it when it has none of its own, and a "[" list
    that runs over several lines goes whole; a trailing "(References: ...)" or
    "References: [...]" goes from the end of a line. Code fences and a leading
    "Answer:" go too.
    """
    kept: list[str] = []
    cited: list[str] = []
    for line, listed in _reference_lines(text.splitlines()):
        if listed is not None:
            cited.extend(_reference_items(listed))
            continue
        trailing = _TRAILING_REFERENCES.search(line) or _TRAILING_REFERENCE_LIST.search(line)
        if trailing is not None:
            cited.extend(_reference_items(trailing.group("items")))
            line = line[: trailing.start()]
        kept.append(line)
    return _ANSWER_LABEL.sub("", "\n".join(kept).strip(), count=1).strip(), cited


def _reference_lines(lines: Iterable[str]) -> Iterator[tuple[str, str | None]]:
    """Each line of a reply as ``(line, None)``, or ``("", list)`` for a references list.

    A references heading and the bullets or ``[...]`` under a bare one belong to
    the list. So does an array written one id to a line, as a model pretty-prints
    it: "References: [" or a bare heading and a "[" line open it, and it runs to
    the line that closes it, or to the end of the reply when that was cut off.
    Its lines are given whole, newlines kept, for ``_reference_items`` to read.
    """
    listing = False
    opened: list[str] = []
    for line in lines:
        if _FENCE_LINE.match(line):
            continue
        if opened or (listing and _opens_list(line)):
            opened.append(line)
            if not _is_open("\n".join(opened)):
                yield "", "\n".join(opened)
                opened = []
            continue
        if listing:
            entry = _LIST_ITEM.match(line)
            if entry is not None or line.strip().startswith("[") or not line.strip():
                yield "", entry.group("item") if entry else line
                continue
            listing = False
        heading = _REFERENCES_LINE.match(line)
        if heading is None:
            yield line, None
            continue
        items = heading.group("items")
        if _opens_list(items):
            opened = [items]
        else:
            yield "", items
            listing = not items.strip()
    if opened:
        yield "", "\n".join(opened)


def _opens_list(text: str) -> bool:
    """Whether ``text`` opens a "[" list that it does not close itself."""
    return text.strip().startswith("[") and _is_open(text)


def _is_open(text: str) -> bool:
    """Whether ``text`` has more "[" than "]", so a list in it is still being written."""
    return text.count("[") > text.count("]")


def _unique(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in items if item.strip()))


def _nodes_by_label(nodes: Mapping[str, GraphNode]) -> dict[str, GraphNode]:
    """Each label exactly one node carries, casefolded; a shared label names nobody."""
    owners: dict[str, dict[str, GraphNode]] = {}
    for node in nodes.values():
        label = node_label(node)
        if label is not None and label.strip():
            owners.setdefault(label.strip().casefold(), {})[node.id] = node
    return {label: next(iter(found.values())) for label, found in owners.items() if len(found) == 1}


def _node_id(
    item: str, nodes: Mapping[str, GraphNode], labelled: Mapping[str, GraphNode]
) -> str | None:
    """The node an item names: its id as written, else the one node with that label.

    "Zoe Almeida (U0AA1ZOE001)" is tried whole, then by the bracketed part,
    then without it.
    """
    candidates = (
        item,
        *(match.group("inner") for match in _BRACKETED.finditer(item)),
        _BRACKETED.sub("", item),
    )
    for candidate in (candidate.strip(_ITEM_EDGES) for candidate in candidates):
        if not candidate:
            continue
        if candidate in nodes:
            return candidate
        node = labelled.get(candidate.casefold())
        if node is not None:
            return node.id
    return None


def _references(
    parsed: ParsedAnswer, nodes: Mapping[str, GraphNode], labelled: Mapping[str, GraphNode]
) -> tuple[str, ...]:
    """The answer's references, as node ids wherever a node matches.

    An id the model wrote stays as written; one it wrote as a label becomes
    the id of the one node with that label. A references-array entry that
    matches nothing stays, so it shows as an id rather than a guessed name; an
    item of a References line that matches nothing is dropped, as that line is
    free text. A line item adds only a node the array does not already name.
    """
    references = [_node_id(item, nodes, labelled) or item for item in parsed.references]
    named = {nodes[reference].id for reference in references if reference in nodes}
    for item in parsed.cited:
        found = _node_id(item, nodes, labelled)
        if found is not None and nodes[found].id not in named:
            references.append(found)
            named.add(nodes[found].id)
    return tuple(dict.fromkeys(references))


def _named_in(answer: str, labelled: Mapping[str, GraphNode]) -> tuple[str, ...]:
    """The nodes an answer names by label, in the order it names them.

    Only for a reply that gave no reference at all, so its sources are not
    empty. A label counts only whole and as written, only when exactly one
    node carries it, and only for the kinds the references rule lists; a
    longer label wins over one inside it ("Digital Platform Program" over a
    "Platform" pod).
    """
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    candidates = sorted(
        (
            (label, node)
            for node in labelled.values()
            if node.kind in _NAMED_KINDS
            and (label := node_label(node)) is not None
            and len(label) >= _MIN_NAMED_LABEL
            and label in answer
        ),
        key=lambda pair: -len(pair[0]),
    )
    for label, node in candidates:
        first: int | None = None
        for match in re.finditer(rf"(?<![\w-]){re.escape(label)}(?![\w-])", answer):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            taken.append((start, end))
            first = start if first is None else first
        if first is not None:
            found.append((first, node.id))
    return tuple(dict.fromkeys(node_id for _, node_id in sorted(found)))


def _tidy_lines(answer: str) -> str:
    """One bullet marker, no trailing spaces, no blank lines between lines."""
    lines = (_BULLET.sub("• ", line.rstrip()) for line in answer.strip().splitlines())
    return "\n".join(line for line in lines if line.strip())


def node_label(node: GraphNode) -> str | None:
    """The words a reader knows a node by -- never its raw id, unless that id is the name.

    An issue's key is how everyone refers to it, so a task reads as its key (its
    name when it has none and its id is no key); a
    merge request as its ref; a person as their display name; everything else
    as its name. A person whose name is only their id has no label.
    """
    if node.kind is NodeKind.TASK:
        # An issue's key names it; a task with no key, and an id that is no key
        # ("task-chk-101"), reads as its name.
        key = _string_metadata(node, "key")
        if key:
            return key
        if _ISSUE_KEY.fullmatch(node.id) or not node.name.strip():
            return node.id
        return node.name.strip()
    if node.kind is NodeKind.WORK_ITEM:
        repo = _string_metadata(node, "repo")
        pr_id = _string_metadata(node, "pr_id")
        if repo and pr_id:
            return f"{repo}#{pr_id}"
    name = node.name.strip()
    if not name or (node.kind is NodeKind.DEVELOPER and name == node.id):
        return None
    return name


async def _nodes_by_any_id(
    repository: GraphRepository, tenant_id: str, as_of: date
) -> dict[str, GraphNode]:
    """Every node of ``as_of`` by its id, and each member also by their chat id.

    A model may cite a person by the chat id a tool showed it. A member's own
    node id always wins over another member's chat id.
    """
    nodes = await repository.list_nodes(tenant_id, as_of=as_of)
    by_id: dict[str, GraphNode] = {}
    for node in nodes:
        chat_id = _string_metadata(node, "chat_external_id")
        if node.kind is NodeKind.DEVELOPER and chat_id is not None:
            by_id.setdefault(chat_id, node)
    by_id.update({node.id: node for node in nodes})
    return by_id


def _source(reference: str, nodes: Mapping[str, GraphNode]) -> AskSource:
    node = nodes.get(reference)
    if node is None:
        return AskSource(id=reference, kind=None, label=None)
    return AskSource(id=reference, kind=node.kind, label=node_label(node))


def _looks_like_an_id(value: str) -> bool:
    """Ids such as U0AA1OMAR01 or pod-data, not a word or a bare number."""
    return (
        len(value) >= 4
        and " " not in value
        and not value.isdigit()
        and any(char.isdigit() or char in "-_:" for char in value)
    )


def _without_raw_ids(answer: str, nodes: Mapping[str, GraphNode]) -> str:
    """Swap any raw id the model wrote into the answer for the node's label.

    The prompt forbids ids in the text; this is the backstop, so a chat id
    or a node id like pod-data never reaches the reader even when the model
    slips. An id that is its own label -- an issue key -- is left alone, and
    "Ana (U123)" becomes "Ana", not the name twice; nor does "U123 (Ana)".
    """
    replacements = sorted(
        (
            (raw_id, label)
            for raw_id, node in nodes.items()
            if raw_id in answer
            and _looks_like_an_id(raw_id)
            and (label := node_label(node)) is not None
            and label != raw_id
        ),
        key=lambda item: -len(item[0]),
    )
    for raw_id, label in replacements:
        escaped = re.escape(raw_id)
        # A literal, so a backslash in a name is never read as a group reference.
        literal = label.replace("\\", "\\\\")
        answer = re.sub(rf"{re.escape(label)}\s*[(\[]\s*`?{escaped}`?\s*[)\]]", literal, answer)
        answer = re.sub(
            rf"(?<![\w/-])`?{escaped}`?\s*[(\[]\s*{re.escape(label)}\s*[)\]]", literal, answer
        )
        answer = re.sub(rf"(?<![\w/-])(`?){escaped}\1(?![\w/-])", literal, answer)
    return answer


def _status_reasons_payload(
    root_id: str,
    tree_nodes: Sequence[TreeNodeView],
    labels: Mapping[str, str | None],
    as_of: date,
) -> dict[str, object]:
    """A node's colour and the reasons behind it, worst first, each naming its source.

    Reasons are grouped the way the Delivery panel groups them: factors that
    say the same thing become one reason naming everyone it applies to.
    """
    context = _ReasonContext(
        root_id=root_id,
        nodes={node.id: node for node in tree_nodes},
        labels=labels,
        # A blocker attributed to an issue cites the issue; its owner is the
        # person whose own status carries the same blocker.
        owners={
            factor.blocker_id: node
            for node in tree_nodes
            if node.kind is NodeKind.DEVELOPER
            for factor in node.factors
            if factor.blocker_id
        },
    )
    root = context.nodes[root_id]
    factors = [factor for factor in root.factors if factor.contributes is not Rag.GREEN] or list(
        root.factors
    )
    factors.sort(key=lambda factor: _RAG_ORDER[factor.contributes])
    reasons: dict[tuple[str, str, str, str | None], dict[str, object]] = {}
    for factor in factors:
        context.add(reasons, factor)
    blockers = {
        factor.blocker_id or factor.description
        for factor in root.factors
        if factor.kind is FactorKind.BLOCKER and not _is_no_reply_placeholder(factor)
    }
    parts = sorted(
        (
            node
            for node in tree_nodes
            if node.id != root_id
            and node.kind in {NodeKind.PROJECT, NodeKind.WORKSTREAM, NodeKind.POD}
        ),
        key=lambda node: (_RAG_ORDER[node.rag or Rag.UNKNOWN], node.kind.value, node.name),
    )
    return {
        "node": {"id": root.id, "kind": root.kind.value, "name": root.name},
        "as_of": as_of.isoformat(),
        "rag": (root.rag or Rag.UNKNOWN).value,
        "source": (root.source or StatusSource.UNKNOWN).value,
        "open_blocker_count": len(blockers),
        "reasons": list(reasons.values())[:_MAX_REASONS],
        "parts": [
            {
                "kind": node.kind.value,
                "id": node.id,
                "name": node.name,
                "rag": (node.rag or Rag.UNKNOWN).value,
            }
            for node in parts[:_MAX_PARTS]
        ],
    }


@dataclass(frozen=True, kw_only=True)
class _ReasonContext:
    root_id: str
    nodes: Mapping[str, TreeNodeView]
    labels: Mapping[str, str | None]
    owners: Mapping[str, TreeNodeView]

    def add(
        self,
        reasons: dict[tuple[str, str, str, str | None], dict[str, object]],
        factor: RollupFactor,
    ) -> None:
        """Fold one factor into the reason that says the same thing.

        The placeholder a silent person's status used to carry ("Blocker: no
        confirmed reply") is a check-in state, not a blocker: the rollup
        stopped counting it, but rows recorded before still hold it, and read
        as written it turned one non-reply into an extra open blocker.
        """
        placeholder = _is_no_reply_placeholder(factor)
        kind = FactorKind.STATUS if placeholder else factor.kind
        says = _NO_REPLY_SAYS if placeholder else (_clip(factor.description) or "")
        cited = self.nodes.get(factor.source_ref.id)
        person = cited if cited is not None and cited.kind is NodeKind.DEVELOPER else None
        if person is None and factor.blocker_id:
            person = self.owners.get(factor.blocker_id)
        checkin = (
            _CHECKIN_WORDS[person.source or StatusSource.UNKNOWN]
            if person is not None and kind is FactorKind.STATUS
            else None
        )
        reason = reasons.setdefault(
            (factor.contributes.value, kind.value, says, checkin),
            {"contributes": factor.contributes.value, "kind": kind.value, "says": says},
        )
        if checkin is not None:
            reason["checkin"] = checkin
        if person is not None:
            _append(reason, "people", {"id": person.id, "name": self.labels.get(person.id)})
        # What the factor is about besides a person: the issue a blocker is on,
        # a task, a workstream's target date. Never the node being explained.
        subject = factor.work_item_ref or (
            factor.source_ref if factor.source_ref.kind is not NodeKind.DEVELOPER else None
        )
        if subject is not None and subject.id != self.root_id:
            _append(
                reason,
                "about",
                {
                    "id": subject.id,
                    "kind": subject.kind.value,
                    "label": self.labels.get(subject.id),
                },
            )


def _signal_payload(
    signal: str,
    kind: str,
    severity: Rag,
    entity_id: str,
    owner_id: str | None,
    reason: str,
    labels: Mapping[str, str | None],
) -> dict[str, object]:
    """A risk or drift finding, about an item by its label and owned by a named person."""
    return {
        "signal": signal,
        "type": kind,
        "severity": severity.value,
        "about": {"id": entity_id, "label": labels.get(entity_id)},
        "owner": {"id": owner_id, "name": labels.get(owner_id)} if owner_id else None,
        "says": _clip(reason),
    }


def _append(reason: dict[str, object], key: str, entry: dict[str, object]) -> None:
    entries = reason.setdefault(key, [])
    if isinstance(entries, list) and entry not in entries:
        entries.append(entry)


def _is_no_reply_placeholder(factor: RollupFactor) -> bool:
    return (
        factor.kind is FactorKind.BLOCKER
        and factor.description.removeprefix("Blocker:").strip().lower() == NO_REPLY_BLOCKER
    )


def _string_metadata(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _workstream_flow_payload(view: WorkstreamFlowView) -> dict[str, object]:
    return {
        "workstream_id": view.workstream_id,
        "workstream_name": view.workstream_name,
        "as_of": view.as_of.isoformat(),
        "active_count": view.active_count,
        "features_in_flight": view.features_in_flight,
        "completed_count": view.completed_count,
        "stale_count": view.stale_count,
        "abandoned_count": view.abandoned_count,
        "avg_cycle_time_days": view.avg_cycle_time_days,
        "avg_pr_age_days": view.avg_pr_age_days,
        "work_items": [
            {
                "id": item.id,
                "name": item.name,
                "state": state_label(item.state),
                "item_type": item.item_type,
                "repo": item.repo,
                "branch": item.branch,
                "pr_id": item.pr_id,
                "workstream_ids": list(item.workstream_ids),
                "age_days": item.age_days,
                "cycle_time_days": item.cycle_time_days,
                "last_transition_at": item.last_transition_at.isoformat()
                if item.last_transition_at is not None
                else None,
            }
            for item in view.work_items
        ],
    }


def _portfolio_flow_payload(view: PortfolioFlowView) -> dict[str, object]:
    return {
        "as_of": view.as_of.isoformat(),
        "active_count": view.active_count,
        "features_in_flight": view.features_in_flight,
        "completed_count": view.completed_count,
        "stale_count": view.stale_count,
        "abandoned_count": view.abandoned_count,
        "avg_cycle_time_days": view.avg_cycle_time_days,
        "avg_pr_age_days": view.avg_pr_age_days,
        "workstreams": [
            {
                "workstream_id": item.workstream_id,
                "workstream_name": item.workstream_name,
                "active_count": item.active_count,
                "features_in_flight": item.features_in_flight,
                "completed_count": item.completed_count,
                "stale_count": item.stale_count,
                "abandoned_count": item.abandoned_count,
                "avg_cycle_time_days": item.avg_cycle_time_days,
                "avg_pr_age_days": item.avg_pr_age_days,
            }
            for item in view.workstreams
        ],
    }


def _workstream_progress_payload(view: WorkstreamProgressView) -> dict[str, object]:
    return {
        "workstream_id": view.workstream_id,
        "workstream_name": view.workstream_name,
        "as_of": view.as_of.isoformat(),
        "rag": view.rag.value,
        "source": view.source.value,
        "confidence": view.confidence,
        "percent_complete": view.percent_complete,
        "total_tasks": view.total_tasks,
        "green_tasks": view.green_tasks,
        "amber_tasks": view.amber_tasks,
        "red_tasks": view.red_tasks,
        "unknown_tasks": view.unknown_tasks,
        "factors": [
            _workstream_factor_payload(factor, view.workstream_id, view.source_names)
            for factor in view.factors[:5]
        ],
        "red_and_amber_tasks": [
            {
                "id": task.id,
                "name": task.name,
                "rag": task.rag.value,
                "source": task.source.value,
                "confidence": task.confidence,
                "deadline": _iso(task.deadline),
            }
            for task in sorted(
                (task for task in view.tasks if task.rag in {Rag.RED, Rag.AMBER}),
                key=lambda task: _RAG_ORDER[task.rag],
            )[:10]
        ],
    }


def _workstream_factor_payload(
    factor: RollupFactor, workstream_id: str, names: Mapping[str, str]
) -> dict[str, object]:
    """A factor, and who or what it is about when that is not the workstream itself."""
    payload: dict[str, object] = {
        "description": _clip(factor.description),
        "contributes": factor.contributes.value,
    }
    cited = factor.source_ref
    if cited.id == workstream_id:
        return payload
    # An issue goes by its key; anything else by its name, never by a raw id.
    about = cited.id if cited.kind is NodeKind.TASK else names.get(cited.id)
    if about is not None and (cited.kind is NodeKind.TASK or about != cited.id):
        payload["about"] = about
    return payload


def _portfolio_heatmap_payload(
    view: PortfolioHeatmapView,
    names: Mapping[str, str],
    *,
    kinds: set[str],
    limit: int,
) -> dict[str, object]:
    cells = sorted(
        (cell for cell in view.cells if not kinds or cell.entity_ref.kind.value in kinds),
        key=lambda cell: (_RAG_ORDER[cell.rag], cell.entity_ref.kind.value, cell.entity_ref.id),
    )
    return {
        "as_of": view.as_of.isoformat(),
        "cell_count": len(cells),
        "cells": [
            {
                "kind": cell.entity_ref.kind.value,
                "id": cell.entity_ref.id,
                "name": names.get(cell.entity_ref.id),
                "rag": cell.rag.value,
                "source": cell.source.value,
                "why": _clip(cell.why),
                "why_ref": {"kind": cell.source_ref.kind.value, "id": cell.source_ref.id},
            }
            for cell in cells[:limit]
        ],
    }


def _risk_payload(risk: RiskFinding, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "severity": risk.severity.value,
        "rule": risk.rule_id.value,
        "entity_kind": risk.entity_ref.kind.value,
        "entity_id": risk.entity_ref.id,
        "entity_name": names.get(risk.entity_ref.id),
        "workstream_id": risk.workstream_id,
        "workstream_name": names.get(risk.workstream_id) if risk.workstream_id else None,
        "reason": _clip(risk.reason),
        "evidence": risk.evidence.identifier,
        "days_open": risk.age_days,
        "detected_on": risk.detected_at.date().isoformat(),
        "owner_id": risk.owner_id,
        "owner_name": names.get(risk.owner_id) if risk.owner_id else None,
        "owner_says": _clip(risk.owner_status_summary),
        "owner_status_source": (
            risk.owner_status_source.value if risk.owner_status_source is not None else None
        ),
        "owner_status_as_of": _iso(risk.owner_status_as_of),
        "owner_has_blockers": risk.owner_status_has_blockers,
        "watermelon": risk.is_watermelon,
    }


def _drift_payload(finding: DriftFinding, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "severity": finding.severity.value,
        "kind": finding.kind.value,
        "entity_kind": finding.entity_ref.kind.value,
        "entity_id": finding.entity_ref.id,
        "entity_name": names.get(finding.entity_ref.id),
        "workstream_id": finding.workstream_id,
        "reason": _clip(finding.reason),
        "detected_on": finding.detected_at.date().isoformat(),
        "owner_id": finding.owner_id,
        "owner_name": names.get(finding.owner_id) if finding.owner_id else None,
        "stated_source": finding.stated_source.value if finding.stated_source else None,
        "evidence": finding.evidence.identifier if finding.evidence is not None else None,
    }


async def _node_names(repository: GraphRepository, tenant_id: str, as_of: date) -> dict[str, str]:
    """Names by node id as of ``as_of``, leaving out a person whose only name is their raw id."""
    return {
        node.id: node.name
        for node in await repository.list_nodes(tenant_id, as_of=as_of)
        if node.name and not (node.kind is NodeKind.DEVELOPER and node.name == node.id)
    }


async def _pods(
    repository: GraphRepository, tenant_id: str, pod_id: JsonScalar
) -> Sequence[GraphNode] | str:
    """The pod asked about, or every pod; an error message when the id is not a pod."""
    requested = _string_argument(pod_id)
    if requested is None:
        return (await repository.list_nodes(tenant_id, NodeKind.POD))[:_MAX_PODS]
    node = await repository.get_node(tenant_id, requested)
    if node is None:
        return f"pod {requested} not found; search_graph_nodes with kinds ['pod'] finds its id"
    if node.kind is not NodeKind.POD:
        return f"{requested} is a {node.kind.value}, not a pod"
    return (node,)


def _readiness_payload(board: BoardView, as_of: date) -> dict[str, object]:
    """A readiness board for the model: every criterion's state, evidence and draft, by name.

    The counts are the board's own (ReadinessBoardResponse.summary); a person is
    named, never given by id; an empty board says why, so it never reads as ready.
    """
    states = [view.shown_state for view in board.findings]
    checked = board.last_run.finished_at if board.last_run else None
    payload: dict[str, object] = {
        "scope_name": board.scope_name,
        "project_id": board.project_id,
        "pod_id": board.pod_id,
        "release_id": board.release_id,
        "checks_on": board.settings.enabled,
        "last_checked": checked.isoformat() if checked else None,
        "jira_data_as_of": board.jira.as_of.isoformat() if board.jira.as_of else None,
        "jira_data_stale": board.jira.stale,
        "blocking_missing": sum(
            1
            for view in board.findings
            if view.shown_state == "missing" and view.criterion.severity is Severity.BLOCKING
        ),
        "missing": states.count("missing"),
        "unsure": states.count("unsure"),
        "covered": states.count("covered"),
        "not_applicable": states.count("not_applicable"),
        "criteria": [_finding_payload(view, board.names) for view in board.findings],
        "releases": [
            {
                "release_id": line.release_id,
                "release_name": line.name,
                "missing": line.missing,
                "unsure": line.unsure,
                "criteria": line.total,
            }
            for line in board.releases[:_MAX_PARTS]
        ],
        "day_report_lines": list(board.important),
    }
    if checked is not None and checked.date() > as_of:
        payload["history"] = (
            f"Release readiness keeps no history: this is the check of {checked.date()}, "
            f"which may not be how it stood on {as_of.isoformat()}."
        )
    if not board.findings:
        payload["no_criteria"] = (
            "No release criterion applies here, so nothing is checked: this says nothing "
            "about whether it is ready."
            if board.settings.enabled
            else "Release readiness checks are off for this tenant, so nothing is checked."
        )
    return payload


_READY_STATES = frozenset({"covered", "not_applicable"})


def what_is_needed(
    criterion: str, needed_before: DeliveryStage, urgency: Urgency, *, ready: bool
) -> str | None:
    """What a criterion still needs before its stage, and by when, in a sentence to quote.

    The finding used to give the requirement that reached the stage as
    requirement_at_stage, beside needed_by, and an answer read the key as the
    thing needed: "(CHK-3 needed by 2026-11-24)". The criterion is what is
    needed; the key is a requirement that already reached the stage without it.
    None once the criterion is covered or not applicable.
    """
    if ready:
        return None
    what = f"The {name_in_sentence(criterion)}"
    stage = stage_words(needed_before)
    due = urgency.due_on
    if urgency.kind is UrgencyKind.OVERDUE and due is not None:
        line = f"{what} was needed before {stage} by {due.isoformat()}, and that day has passed"
    elif due is not None:
        line = f"{what} is needed before {stage}, by {due.isoformat()}"
    else:
        line = f"{what} is needed before {stage}"
    if urgency.kind is UrgencyKind.STAGE_REACHED and urgency.stage_key:
        line += f"; {urgency.stage_key} already reached {stage} without it"
    return line + "."


def _finding_payload(view: FindingView, names: Mapping[str, str]) -> dict[str, object]:
    finding, criterion, suggestion = view.finding, view.criterion, view.suggestion
    person = finding.person
    urgency = finding.urgency
    entry: dict[str, object] = {
        "criterion": criterion.name,
        "severity": criterion.severity.value,
        "needed_before": stage_words(criterion.required_before),
        "scope_kind": finding.scope.kind.value,
        "scope_name": view.scope_name,
        "state": state_label(view.shown_state),
        "reason": _clip(finding.reason),
        "evidence": [
            {
                "issue_key": item.issue_key or None,
                "title": _clip(item.title),
                "status": state_label(item.status) if item.status else None,
                "done": item.done,
                "how_found": item.how,
            }
            for item in finding.evidence[:_MAX_SIGNALS]
        ],
        "candidates": [
            {"issue_key": item.issue_key, "title": _clip(item.title), "why": item.why.value}
            for item in finding.candidates[:_MAX_SIGNALS]
        ],
        "urgency": urgency.kind.value,
        "what_is_needed": what_is_needed(
            criterion.name,
            criterion.required_before,
            urgency,
            ready=view.shown_state in _READY_STATES,
        ),
        "needed_by": _iso(urgency.due_on),
        "working_days_left": view.working_days_left,
        "reached_stage_without_it": (
            urgency.stage_key if urgency.kind is UrgencyKind.STAGE_REACHED else None
        ),
        "held_for_fresh_jira_data": finding.held,
    }
    if person is not None:
        entry["decision"] = {
            "kind": state_label(person.kind.value),
            "by": names.get(person.by),
            "reason": _clip(person.reason) or None,
            "issue_key": person.issue_key or None,
        }
    if suggestion is not None:
        entry["draft"] = {
            "status": state_label(suggestion.status.value),
            "summary": _clip(suggestion.draft.summary),
            "jira_project": suggestion.draft.project_key,
            "created_issue_key": suggestion.created_issue_key,
        }
    return entry


def _forecast_payload(view: ScopeDeliveryView) -> dict[str, object]:
    """One scope's date and forecast, as Delivery shows it, with its reasons."""
    history = view.history
    team = view.team
    return {
        **_forecast_summary(view),
        "first_committed_date": _iso(view.commitment.original_date),
        "times_moved": view.commitment.times_moved,
        "target_source": _TARGET_SOURCES.get(view.target_source or ""),
        "verdict_rests_on": _verdict_basis(view),
        "enough_history": history.sample_days >= history.needed_days,
        "history_working_days": history.sample_days,
        "history_working_days_needed": history.needed_days,
        "no_forecast_reason": history.reason,
        "remaining": _number(history.remaining),
        "unit": history.unit,
        "finished_in_history": _number(history.completed_in_sample),
        "team_latest_date": _iso(team.latest),
        "team_latest_issue": team.latest_key,
        # The count and the keys of the same requirements: Investigate once said
        # one requirement had no ETA while Delivery showed six.
        "open_without_date": team.undated,
        "open_without_date_keys": list(team.undated_keys[:_MAX_UNDATED_KEYS]),
        "requirements": view.total,
        # Its reasons would say every requirement is in production.
        "reasons": (
            [_clip(reason) for reason in view.reasons[:_MAX_REASONS]]
            if view.total > 0
            else [_NOTHING_COUNTED_REASON]
        ),
    }


def _forecast_summary(view: ScopeDeliveryView) -> dict[str, object]:
    """The date, verdict and history's dates: what a project lists for each of its parts."""
    return {
        "committed_date": _iso(view.commitment.target_date),
        "target_date": _iso(view.target),
        "verdict": _VERDICT_WORDS[view.verdict] if view.total > 0 else _NOTHING_TO_FORECAST,
        "forecast_p50": _iso(view.history.p50) if view.history.remaining > 0 else None,
        "forecast_p85": _iso(view.history.p85) if view.history.remaining > 0 else None,
        "open_requirements": view.open,
    }


def _verdict_basis(view: ScopeDeliveryView) -> str | None:
    """What decided an on track, at risk or off track verdict: history first, else the team."""
    if view.verdict not in _JUDGED or view.total == 0:
        return None
    if view.history.p50 is not None and view.history.p85 is not None:
        return "history forecast"
    return "team ETAs and due dates"


def _number(value: float) -> float | int:
    return int(value) if value.is_integer() else round(value, 1)


def _count_words(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _clip(text: str | None) -> str | None:
    """Bound free text so one long summary cannot crowd out the rest of a result."""
    if text is None or len(text) <= _MAX_TEXT_CHARS:
        return text
    return text[: _MAX_TEXT_CHARS - 1].rstrip() + "…"


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot_date(value: JsonScalar, as_of: date) -> date:
    """The day a snapshot tool reads: the as-of date, or an earlier one asked for.

    A question asked as of a day cannot see past it, so a later date -- the
    model's own idea of today, say -- falls back to the as-of date.
    """
    requested = _optional_date(value)
    return requested if requested is not None and requested <= as_of else as_of


def _required_string(value: JsonScalar, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{field} must be provided")


def _string_argument(value: JsonScalar) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list_argument(value: JsonScalar) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in (_string_argument(item) for item in value) if item]
    item = _string_argument(value)
    return [item] if item else []


def _kinds_argument(value: JsonScalar) -> list[str]:
    return _string_list_argument(value)


def _edge_kinds_argument(value: JsonScalar) -> list[EdgeKind]:
    """Parse requested edge kinds, ignoring any the graph does not have."""
    kinds: list[EdgeKind] = []
    for item in _string_list_argument(value):
        try:
            kinds.append(EdgeKind(item.strip().lower()))
        except ValueError:
            continue
    return kinds


def _optional_date(value: JsonScalar) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _bounded_int(
    value: JsonScalar,
    *,
    default: int,
    maximum: int,
) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, maximum)
    return min(default, maximum)


def _metadata_text(node: GraphNode) -> str:
    values = [str(value).lower() for value in node.metadata.values() if value is not None]
    return " ".join([node.id.lower(), node.name.lower(), *values])


def _impact_period(arguments: Mapping[str, JsonScalar]) -> ImpactPeriod:
    """The period delivery_impact covers: a named day, a named period, else since planning."""
    start = _optional_date(arguments.get("from"))
    if start is not None:
        return ImpactPeriod(kind=PeriodKind.FROM_DAY, from_day=start)
    named = _string_argument(arguments.get("period"))
    try:
        kind = PeriodKind(named) if named else PeriodKind.SINCE_BASELINE
    except ValueError:
        kind = PeriodKind.SINCE_BASELINE
    if kind is PeriodKind.FROM_DAY:
        kind = PeriodKind.SINCE_BASELINE
    return ImpactPeriod(kind=kind)


def _measure(value: JsonScalar) -> Measure:
    named = _string_argument(value)
    return Measure.P50 if named == Measure.P50.value else Measure.P85


#: The three dates delivery_impact speaks of, kept apart for the model: a team's date
#: or a likely date that moved is never "the project's date moved".
IMPACT_DATES: Mapping[str, str] = {
    "committed_date": (
        "The date people committed to deliver by. Only a person moves it; each move is "
        "in committed_changes, with who and why."
    ),
    "likely_date": (
        "When the forecast says the work will likely be done, from how fast the team "
        "finishes: likely_by (85% likely) and likely_around (50%). It moves with the "
        "causes in drivers; nobody sets it."
    ),
    "team_date": (
        "The team's own date: the latest finish date the team gave (ETA or due date) "
        "across the open requirements. It is neither the committed date nor the likely date."
    ),
}


def impact_first_line(not_known: Sequence[str]) -> str:
    """The returned text's first line: what is not known, always, even when nothing is."""
    if not not_known:
        return "Not known: nothing; the likely date is known at both ends of the period."
    said = "; ".join(f"{note[:1].lower()}{note[1:]}".rstrip(".") for note in not_known)
    return f"Not known: {said}."


def _impact_payload(view: ImpactView) -> dict[str, object]:
    """One scope's move, taken apart, for the model: names beside ids, keys and dates only.

    What is not known comes first (``not_known`` and the summary's first line), so an
    answer never drops it; the three dates are named apart (``dates``)."""
    first_line = impact_first_line(view.not_known)
    payload: dict[str, object] = {
        "not_known": list(view.not_known),
        "summary": f"{first_line}\n{view.summary}",
        "dates": dict(IMPACT_DATES),
        "scope": {
            "kind": view.scope.kind.value,
            "id": view.scope.id,
            "name": view.name,
            "project_id": view.scope.project_id,
            "project_name": view.project_name,
        },
        "period": view.period.value,
        "period_words": view.period_words,
        "measure": view.measure.value,
        "planning_complete_on": _iso(view.baseline_day),
        "change_label": view.change_label,
        # The likely date and the committed date at the start of the period and now.
        "then": _impact_end(view.start),
        "now": _impact_end(view.end),
        "likely_date_moved_working_days": view.moved_working_days,
        "unit": view.unit,
        "drivers": [
            {
                "kind": driver.kind,
                **({"group": driver.group.value} if driver.group is not None else {}),
                "working_days": driver.working_days,
                "note": driver.note,
                **(
                    {"items": [_impact_item(item) for item in driver.items]} if driver.items else {}
                ),
                **({"more_items": driver.more} if driver.more else {}),
            }
            for driver in view.drivers
        ],
        "scope_changes": {
            "added": view.scope_changes.added_count,
            "removed": view.scope_changes.removed_count,
            "reopened": view.scope_changes.reopened_count,
            "finished": view.scope_changes.finished_count,
            **(
                {"added_items": [_impact_item(item) for item in view.scope_changes.added]}
                if view.scope_changes.added and not view.drivers
                else {}
            ),
        },
        "committed_changes": [
            {
                "from": _iso(change.from_date),
                "to": _iso(change.to_date),
                "on": change.on.isoformat(),
                "by": change.by,
                "note": _clip(change.note) or "",
                **({"after_added": list(change.after_added)} if change.after_added else {}),
            }
            for change in view.committed_changes
        ],
        "team_date": (
            {
                "team_date_then": _iso(view.team_date.from_date),
                "then_issue": view.team_date.from_key,
                "team_date_now": _iso(view.team_date.to_date),
                "now_issue": view.team_date.to_key,
                "moves": [
                    {"day": move.day.isoformat(), "cause": move.cause, "words": move.words}
                    for move in view.team_date.moves
                ],
            }
            if view.team_date is not None
            else None
        ),
        "markers": [
            {
                "day": marker.day.isoformat(),
                "kind": marker.kind,
                "count": marker.count,
                "keys": [item.key for item in marker.items],
                **(
                    {"change_label": marker.change_label} if marker.change_label is not None else {}
                ),
            }
            for marker in view.markers
        ],
        "link": view.link,
        "reconstructed_days": view.reconstructed_days,
    }
    if view.evidence is not None:
        payload["evidence"] = {
            "late": [
                {
                    "key": item.key,
                    "title": _clip(item.title),
                    "stage": STAGE_LABELS[item.stage],
                    "date": item.date.isoformat(),
                    "date_kind": item.date_kind,
                    "working_days_late": item.working_days_late,
                }
                for item in view.evidence.late
            ],
            **({"more_late": view.evidence.more_late} if view.evidence.more_late else {}),
            "undated": list(view.evidence.undated[:_MAX_UNDATED_KEYS]),
        }
    return payload


def _impact_end(end: ImpactEnd | None) -> dict[str, object] | None:
    if end is None:
        return None
    return {
        "day": end.day.isoformat(),
        "likely_by": _iso(end.p85),
        "likely_around": _iso(end.p50),
        "committed_date": _iso(end.committed),
    }


def _impact_item(item: ImpactItemView) -> dict[str, object]:
    found: dict[str, object] = {
        "key": item.key,
        "title": _clip(item.title),
        "working_days": item.working_days,
        "day": _iso(item.day),
    }
    if item.type:
        found["type"] = item.type
    if item.group is not None:
        found["group"] = item.group.value
    if item.change_label is not None:
        found["change_label"] = item.change_label
    if item.added_by:
        found["added_by"] = item.added_by
    return found
