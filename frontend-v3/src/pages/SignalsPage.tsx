import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { PortfolioRisksResponse, ProjectRisksResponse } from "../api/schema";
import type { Scope, SignalsView } from "../app/access";
import {
  podsOfPerson,
  projectsOfPerson,
  rankProjects,
  useMemberId,
  useNames,
  usePods,
  useProjects,
} from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { ChipPicker, Panel, RagDot } from "../components/ui/Bits";
import { RagChip } from "../components/ui/RagChip";
import { formatDay } from "../lib/format";
import { readState } from "../lib/readState";
import { PrFlowSection } from "../features/signals/PrFlowSection";
import {
  ageWords,
  groupByProject,
  ownerLine,
  riskCountLine,
  type Finding,
} from "../features/signals/signalWords";

const VIEW_LABELS: Record<SignalsView, string> = { risks: "Risks", flow: "Flow" };

/**
 * What Jira and Git say, independent of what anyone reports: the flow of work
 * through review, and, for the portfolio roles, one list of risks across
 * projects. A scrum master's risks are the pod reasons on their Today and a
 * product owner's are Overall's, so Signals is Flow for them. What changed is
 * the briefs' job and Ask the graph's, so there is no activity feed here.
 */
export function SignalsPage() {
  const { signals } = useRole().access;
  const [search, setSearch] = useSearchParams();
  const asked = search.get("view") as SignalsView | null;
  const view: SignalsView = asked && signals.views.includes(asked) ? asked : signals.defaultView;
  const showsRisks = signals.views.includes("risks");
  const portfolio = useQuery({
    queryKey: ["portfolio", "risks"],
    queryFn: () => apiClient.portfolioRisks(),
    enabled: showsRisks,
  });
  const counts = portfolio.data
    ? riskCountLine(portfolio.data.risks.length, portfolio.data.drift.length)
    : null;

  return (
    <>
      <SectionHeader
        title="Signals"
        meta={
          showsRisks
            ? (counts ?? "Risks across projects, and the flow of work through review.")
            : "How long work waits in review, from Git, independent of what anyone reports."
        }
      />
      {signals.views.length > 1 ? (
        <ChipPicker
          label="Show"
          value={view}
          onChange={(next) =>
            setSearch(
              (current) => {
                // The flow's scope, window and percentile belong to the flow view; the day stays.
                const params = new URLSearchParams();
                const day = current.get("asOf");
                if (day) params.set("asOf", day);
                if (next !== signals.defaultView) params.set("view", next);
                return params;
              },
              { replace: true },
            )
          }
          options={signals.views.map((value) => ({
            value,
            label:
              value === "risks" && portfolio.data
                ? `Risks · ${portfolio.data.risks.length + portfolio.data.drift.length}`
                : VIEW_LABELS[value],
          }))}
        />
      ) : null}
      {view === "flow" ? <Flow scope={signals.flowScope} /> : null}
      {view === "risks" ? <RisksAcrossProjects portfolio={portfolio} /> : null}
    </>
  );
}

/**
 * The flow through review, opening on the viewer's own part: a scrum master's
 * first pod, a product owner's first project, everything for the portfolio
 * roles. "All repositories" is a pick away, and the pick lives in the URL.
 */
function Flow({ scope }: { scope: Scope }) {
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const own = podsOfPerson(pods.data ?? [], memberId);
  const ownProjects = projectsOfPerson(projects.data ?? [], own.pods, own.own);
  const waiting =
    scope !== "all" && (pods.isPending || (scope === "projects" && projects.isPending));
  const firstPod = own.own ? own.pods[0] : undefined;
  const firstProject = ownProjects.own
    ? rankProjects(ownProjects.projects, own.pods)[0]
    : undefined;
  const opensOn =
    scope === "pods" && firstPod
      ? `pod:${firstPod.id}`
      : scope === "projects" && firstProject
        ? `project:${firstProject.id}`
        : "all";
  if (waiting) {
    return (
      <PanelState isLoading error={null}>
        {null}
      </PanelState>
    );
  }
  return <PrFlowSection full enabled defaultScope={opensOn} />;
}

/**
 * Every open risk and drift finding, grouped by project, the worst project
 * first. Each is one row: its severity, the reason (which names the merge
 * request or issue), its age, one grey line of what its owner says, and the
 * evidence. Colour is spent only on the severity dot.
 */
function RisksAcrossProjects({ portfolio }: { portfolio: UseQueryResult<PortfolioRisksResponse> }) {
  const projects = useProjects();
  const list = projects.data ?? [];
  // The same key as Overall's risks, so a project read there is not asked again.
  const reads = useQueries({
    queries: list.map((project) => ({
      queryKey: ["risks", project.id],
      queryFn: () => apiClient.projectRisks(project.id),
    })),
  });
  const byProject = new Map<string, ProjectRisksResponse | undefined>(
    list.map((project, index) => [project.id, reads[index]?.data]),
  );
  const loading = projects.isLoading || portfolio.isLoading || reads.some((read) => read.isLoading);
  // A project whose own read failed keeps its findings in the portfolio's list, under
  // "Not tied to a project"; a line says which projects those may belong to.
  const unread = list.filter((_, index) => reads[index]?.error).map((project) => project.name);
  const groups = groupByProject(list, (id) => byProject.get(id), portfolio.data);
  const state = readState(portfolio, projects);

  return (
    <PanelState
      isLoading={loading || state.isLoading}
      error={state.error}
      onRetry={() => void portfolio.refetch()}
      isEmpty={groups.length === 0}
      emptyText="No open risks or drift. Nothing in Jira or Git disagrees with what was reported."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        {unread.length > 0 ? (
          <p className="text-[12px] text-grey-secondary">
            The risks of {unread.join(", ")} could not be read by project, so any of theirs are
            listed under &ldquo;Not tied to a project&rdquo;.
          </p>
        ) : null}
        {groups.map((group) => (
          <Panel
            key={group.projectId ?? "none"}
            title={
              <span className="inline-flex items-center gap-2">
                <RagDot rag={group.worst} />
                {group.name}
              </span>
            }
            note={`${group.findings.length} ${group.findings.length === 1 ? "finding" : "findings"}`}
          >
            <ul>
              {group.findings.map((item) => (
                <FindingRow key={item.key} item={item} />
              ))}
            </ul>
          </Panel>
        ))}
      </div>
    </PanelState>
  );
}

function FindingRow({ item }: { item: Finding }) {
  const names = useNames();
  const f = item.finding;
  const who =
    item.type === "risk"
      ? (item.finding.person_name ?? (item.finding.owner_id ? names(item.finding.owner_id) : null))
      : item.finding.owner_id
        ? names(item.finding.owner_id)
        : null;
  const evidence = f.evidence;
  return (
    <li className="flex min-w-0 items-start gap-3 border-t border-grey-border py-3 first:border-t-0">
      <RagDot rag={item.severity} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-[14px] font-bold text-ink">
          <span className="min-w-0">{f.reason}</span>
          {item.type === "drift" ? (
            <RagChip tone="neutral" className="h-5 px-2 text-[11px]">
              drift
            </RagChip>
          ) : null}
        </p>
        <p className="mt-0.5 text-[12px] text-grey-secondary">{ownerLine(item, who)}</p>
        {evidence ? (
          <p className="mt-0.5 text-[12px]">
            {evidence.url ? (
              <a href={evidence.url} target="_blank" rel="noreferrer" className="font-bold">
                {evidence.identifier}
              </a>
            ) : (
              <span className="text-grey-secondary">{evidence.identifier}</span>
            )}
          </p>
        ) : null}
      </div>
      <span className="flex-none text-right text-[12px] font-bold text-grey-secondary tabular-nums">
        {ageWords(item, formatDay)}
      </span>
    </li>
  );
}
