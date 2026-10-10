import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  ReadinessBoardResponse,
  ReadinessDraftDto,
  ReadinessFindingResponse,
} from "../../api/schema";
import { useRole } from "../../app/role";
import { useReadOnly } from "../../app/viewingDate";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { Fold } from "../../components/viz/Fold";
import { STAGE_LABELS } from "../../components/viz/stages";
import { formatDay, formatTime } from "../../lib/format";
import { cn } from "../../lib/utils";
import { actionError } from "../reports/access";
import { FormProblem, Locked, ReportDialog, field, fieldLabel } from "../reports/ReportDialog";
import { useReadiness } from "./queries";
import {
  ACTION_WORDS,
  actorWords,
  answerLine,
  decisionWords,
  draftFacts,
  footerWords,
  linkTarget,
  lowerFirst,
  reasonProblem,
  releaseLineWords,
  rowGroups,
  shortDay,
  stateLine,
  stateTone,
  urgentLine,
} from "./readinessWords";
import { VizCard } from "./viz";

type Finding = ReadinessFindingResponse;

/** Every read an action moves: the board (and the answer card's reason) and today's report. */
function useRefresh(projectId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["readiness", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
    ]);
}

/**
 * Release readiness on Overall (`#readiness`): what the release (or the project,
 * while it has none) and its pods need before production beyond each
 * requirement's gates, checked against Jira every hour. One answer line, then a
 * table of criteria; a missing row folds out its drafted Jira issue, and every
 * row the actions the server says this person has. A tenant that has not set
 * readiness up gets no section at all. `answered`: the answer card above already
 * names the most urgent gap, so this one does not say it again.
 */
export function ReadinessSection({
  projectId,
  releaseId = "",
  answered = false,
}: {
  projectId: string;
  releaseId?: string;
  answered?: boolean;
}) {
  const { access } = useRole();
  const { readOnly, reason } = useReadOnly();
  const { readable, query } = useReadiness(projectId, releaseId || undefined);
  const refresh = useRefresh(projectId);
  const run = useMutation({
    mutationFn: () => apiClient.runReadiness(projectId, releaseId || undefined),
    onSuccess: async (result) => {
      await refresh();
      const { missing, unsure, changed } = result.run;
      toast.success(
        `Checked: ${missing} missing, ${unsure} unsure${changed ? `, ${changed} changed` : ", nothing changed"}.`,
      );
    },
    onError: (error) => toast.error(actionError(error)),
  });
  if (!readable) return null;
  const data = query.data;
  // Not set up for this tenant: nothing to read or do here.
  if (data && !data.agent.enabled && data.findings.length === 0) return null;

  return (
    <section
      id="readiness"
      aria-labelledby="readiness-h"
      className="scroll-mt-24 [contain-intrinsic-size:auto_480px] [content-visibility:auto]"
    >
      <SectionHeader
        id="readiness-h"
        title="Release readiness"
        meta={`What ${releaseId ? "this release" : "the project"} needs before production beyond each requirement's gates. Checked against Jira every hour.`}
        actions={
          data?.can_run && !readOnly ? (
            <Pill size="sm" variant="ghost" disabled={run.isPending} onClick={() => run.mutate()}>
              {run.isPending ? "Checking…" : "Run check now"}
            </Pill>
          ) : data?.can_run && readOnly && reason ? (
            <Locked>{reason}</Locked>
          ) : undefined
        }
      />
      <PanelState
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={data !== undefined && data.findings.length === 0 && data.releases.length === 0}
        emptyText="Nothing checked yet. The check runs every hour after the Jira sync."
      >
        {data ? (
          <Board
            data={data}
            projectId={projectId}
            answered={answered}
            canAct={access.readiness.act && !readOnly}
          />
        ) : null}
      </PanelState>
    </section>
  );
}

function Board({
  data,
  projectId,
  answered,
  canAct,
}: {
  data: ReadinessBoardResponse;
  projectId: string;
  answered: boolean;
  canAct: boolean;
}) {
  const urgent = answered ? null : urgentLine(data);
  const groups = rowGroups(data.findings);
  const held = data.findings.some((item) => item.held);
  return (
    <VizCard className="gap-3">
      {data.findings.length > 0 ? (
        <div className="grid gap-1">
          <h3 className="text-[17px] font-extrabold leading-snug text-balance">
            {answerLine(data, data.scope_name)}
          </h3>
          {urgent ? <p className="text-[14px] font-bold text-rag-red">{urgent}</p> : null}
        </div>
      ) : null}
      {data.releases.length > 0 ? (
        <ul className="flex flex-wrap gap-2" aria-label="Each release">
          {data.releases.map((line) => (
            <li key={line.release_id}>
              <Link
                to={`?release=${encodeURIComponent(line.release_id)}#readiness`}
                className="inline-flex min-h-9 items-center rounded-full border border-grey-border px-3.5 text-[13px] font-bold hover:bg-grey-fill"
              >
                {releaseLineWords(line)}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      {data.findings.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-separate border-spacing-0 text-[13px]">
            <caption className="sr-only">
              Release criteria and where each stands: blocking first, then missing, unsure and
              covered
            </caption>
            <thead>
              <tr>
                <th scope="col" className={headCell}>
                  Criterion
                </th>
                <th scope="col" className={cn(headCell, "max-sm:hidden")}>
                  Before
                </th>
                <th scope="col" className={cn(headCell, "max-sm:hidden")}>
                  State
                </th>
                <th scope="col" className={cn(headCell, "max-md:hidden")}>
                  Evidence
                </th>
              </tr>
            </thead>
            {groups.map((group) => (
              <GroupRows
                key={group.heading ?? "scope"}
                heading={group.heading}
                rows={group.rows}
                projectId={projectId}
                canAct={canAct}
              />
            ))}
          </table>
        </div>
      ) : null}
      <p className="text-[12px] text-grey-secondary">
        {footerWords(data.agent, moment)}
        {held && !data.agent.stale
          ? " A gap read while the Jira sync was behind waits for fresh data before the day report says it."
          : ""}
      </p>
    </VizCard>
  );
}

/** "07:45" today, "Fri 2 Oct 16:30" on another day: when Jira was last read matters. */
function moment(iso: string): string {
  const day = new Date(iso);
  return day.toDateString() === new Date().toDateString()
    ? formatTime(iso)
    : `${formatDay(iso)} ${formatTime(iso)}`;
}

const headCell =
  "border-b border-grey-border px-2 pb-1.5 text-left text-[11px] font-bold uppercase tracking-wide text-grey-secondary";

function GroupRows({
  heading,
  rows,
  projectId,
  canAct,
}: {
  heading: string | null;
  rows: Finding[];
  projectId: string;
  canAct: boolean;
}) {
  return (
    <tbody>
      {heading ? (
        <tr>
          <th
            scope="rowgroup"
            colSpan={4}
            className="px-2 pb-1 pt-4 text-left text-[13px] font-extrabold text-ink"
          >
            {heading}
          </th>
        </tr>
      ) : null}
      {rows.map((finding) => (
        <FindingRows
          key={finding.finding_id}
          finding={finding}
          projectId={projectId}
          canAct={canAct}
        />
      ))}
    </tbody>
  );
}

function FindingRows({
  finding,
  projectId,
  canAct,
}: {
  finding: Finding;
  projectId: string;
  canAct: boolean;
}) {
  const [open, setOpen] = useState(false);
  const draft = finding.suggestion?.status === "open" ? finding.suggestion : null;
  const toggle =
    finding.state === "missing" && draft
      ? "Suggested issue"
      : finding.state === "unsure" && canAct && finding.can.link
        ? "Link or decide"
        : "Details";
  const detailId = `readiness-${finding.finding_id}`;
  return (
    <>
      <tr>
        <th scope="row" className="border-b border-grey-border px-2 py-2.5 text-left align-top">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-[14px] font-extrabold text-ink">{finding.criterion.name}</span>
            {finding.criterion.severity === "blocking" ? (
              <span className="rounded-md bg-grey-fill px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-wide text-grey-body">
                Blocking
              </span>
            ) : null}
          </span>
          <span className="mt-1 block sm:hidden">
            <StateChip finding={finding} />
          </span>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={detailId}
            className="mt-1 block text-[12px] font-bold text-grey-body underline-offset-2 hover:underline max-sm:min-h-9"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Hide" : toggle}
          </button>
        </th>
        <td className="border-b border-grey-border px-2 py-2.5 align-top text-grey-body max-sm:hidden">
          {STAGE_LABELS[finding.criterion.required_before]}
        </td>
        <td className="border-b border-grey-border px-2 py-2.5 align-top max-sm:hidden">
          <StateChip finding={finding} />
          {finding.held ? (
            <span className="mt-1 block text-[11.5px] text-grey-secondary">
              Read while the Jira sync was behind
            </span>
          ) : null}
        </td>
        <td className="border-b border-grey-border px-2 py-2.5 align-top max-md:hidden">
          <EvidenceList finding={finding} />
        </td>
      </tr>
      {open ? (
        <tr id={detailId}>
          <td colSpan={4} className="border-b border-grey-border bg-grey-fill/40 px-2 py-3">
            <Details finding={finding} projectId={projectId} canAct={canAct} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function StateChip({ finding }: { finding: Finding }) {
  return (
    <RagChip
      tone={stateTone(finding)}
      className="h-auto min-h-6 whitespace-normal px-2.5 py-0.5 text-[12px]"
    >
      {stateLine(finding)}
    </RagChip>
  );
}

function EvidenceList({ finding }: { finding: Finding }) {
  if (finding.evidence.length > 0) {
    return (
      <ul className="grid gap-0.5">
        {finding.evidence.map((item) => (
          <li key={item.issue_key || item.url} className="text-[12.5px]">
            {item.issue_key ? (
              <>
                <b className="text-ink">{item.issue_key}</b>
                {item.status ? <span className="text-grey-secondary"> · {item.status}</span> : null}
              </>
            ) : (
              <a href={item.url} className="font-bold underline" rel="noreferrer" target="_blank">
                {item.note || "A record outside Jira"}
              </a>
            )}
          </li>
        ))}
      </ul>
    );
  }
  if (finding.candidates.length > 0) {
    return (
      <span className="text-[12.5px] text-grey-body">
        {finding.candidates.map((item) => item.issue_key).join(", ")}?
      </span>
    );
  }
  return <span className="text-grey-secondary">—</span>;
}

/** A row folded out: why, the draft, what a person may do, and what happened. */
function Details({
  finding,
  projectId,
  canAct,
}: {
  finding: Finding;
  projectId: string;
  canAct: boolean;
}) {
  const refresh = useRefresh(projectId);
  const [dialog, setDialog] = useState<"link" | "waive" | "dismiss" | "edit" | null>(null);
  const can = canAct ? finding.can : null;
  const suggestion = finding.suggestion;
  const draft = suggestion?.status === "open" ? suggestion : null;
  const act = useMutation({
    mutationFn: (step: () => Promise<unknown>) => step(),
    onSuccess: async () => {
      await refresh();
    },
    onError: (error) => toast.error(actionError(error)),
  });
  const decided = decisionWords(finding.person_decision);
  const create = () =>
    act.mutate(async () => {
      if (!draft) return;
      const result = await apiClient.createReadinessIssue(draft.suggestion_id, draft.version);
      toast.success(
        result.created
          ? `Created ${result.issue_key} in Jira. It covers ${finding.criterion.name} now.`
          : `${result.issue_key} was already in Jira; it covers ${finding.criterion.name}.`,
      );
    });

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 text-[13px]">
      <p className="text-grey-body">
        <b className="text-ink">What counts:</b> {finding.criterion.evidence}
      </p>
      {finding.reason && finding.state !== "not_applicable" ? (
        <p className="text-grey-body">{finding.reason}</p>
      ) : null}
      {finding.candidates.length > 0 ? (
        <ul className="grid gap-1">
          {finding.candidates.map((item) => (
            <li key={item.issue_key} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0">
                <b>{item.issue_key}</b> {item.title}
              </span>
              {can?.link ? (
                <Pill
                  size="sm"
                  variant="ghost"
                  className="h-8 px-3"
                  disabled={act.isPending}
                  onClick={() =>
                    act.mutate(async () => {
                      await apiClient.linkReadiness(finding.finding_id, {
                        issue_key: item.issue_key,
                        note: "",
                      });
                      toast.success(
                        `Linked ${item.issue_key}: it covers ${finding.criterion.name}.`,
                      );
                    })
                  }
                >
                  Link {item.issue_key}
                </Pill>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {finding.evidence.length > 0 ? (
        <div className="md:hidden">
          <EvidenceList finding={finding} />
        </div>
      ) : null}
      {decided ? <p className="font-bold text-ink">{decided}</p> : null}
      {suggestion?.status === "dismissed" && suggestion.dismissed ? (
        <p className="text-grey-body">
          The draft was dismissed by {suggestion.dismissed.by_name ?? suggestion.dismissed.by}
          {suggestion.dismissed.at ? `, ${shortDay(suggestion.dismissed.at)}` : ""}:{" "}
          {suggestion.dismissed.reason}. It is not drafted again until someone reopens it.
        </p>
      ) : null}
      {draft && finding.state === "missing" ? (
        <DraftCard draft={draft.draft} marker={draft.marker_label} />
      ) : null}
      {can ? (
        <div className="flex flex-wrap items-center gap-2">
          {can.create && draft ? (
            <ConfirmDialog
              trigger={
                <Pill size="sm" disabled={act.isPending}>
                  Create in Jira
                </Pill>
              }
              title={`Create "${draft.draft.summary}" in Jira?`}
              description={`It goes to ${draft.draft.project_key} as a ${draft.draft.issue_type}, unassigned, with you as the reporter where Jira allows. OpenProgram adds who approved it and a link back.`}
              confirmLabel="Create in Jira"
              onConfirm={create}
            />
          ) : null}
          {can.edit && draft ? (
            <Pill size="sm" variant="ghost" onClick={() => setDialog("edit")}>
              Edit
            </Pill>
          ) : null}
          {can.link ? (
            <Pill size="sm" variant="ghost" onClick={() => setDialog("link")}>
              {finding.candidates.length > 0 ? "Link another" : "Link an existing issue"}
            </Pill>
          ) : null}
          {can.not_applicable ? (
            <Pill size="sm" variant="ghost" onClick={() => setDialog("waive")}>
              Not applicable…
            </Pill>
          ) : null}
          {can.dismiss && draft ? (
            <Pill size="sm" variant="ghost" onClick={() => setDialog("dismiss")}>
              Dismiss…
            </Pill>
          ) : null}
          {can.draft ? (
            <Pill
              size="sm"
              variant="ghost"
              disabled={act.isPending}
              onClick={() =>
                act.mutate(async () => {
                  await apiClient.draftReadiness(finding.finding_id);
                  toast.success("Drafted a Jira issue for it.");
                })
              }
            >
              Draft an issue
            </Pill>
          ) : null}
          {can.reopen ? (
            <Pill
              size="sm"
              variant="ghost"
              disabled={act.isPending}
              onClick={() =>
                act.mutate(async () => {
                  await apiClient.reopenReadiness(finding.finding_id);
                  toast.success("Reopened: the check decides it again.");
                })
              }
            >
              Reopen
            </Pill>
          ) : null}
          {draft && can.create_off_reason ? (
            <span className="text-[12px] text-grey-secondary">{can.create_off_reason}</span>
          ) : null}
        </div>
      ) : null}
      <Fold summary="What happened">
        <History findingId={finding.finding_id} />
      </Fold>
      {dialog === "link" ? (
        <LinkDialog finding={finding} onClose={() => setDialog(null)} onDone={refresh} />
      ) : null}
      {dialog === "waive" ? (
        <ReasonDialog
          title={`${finding.criterion.name}: not applicable to ${finding.scope.name}?`}
          description="It no longer counts for this scope, and the day report stops asking for it. Reopen undoes it."
          label="Why it does not apply"
          confirm="Mark not applicable"
          onClose={() => setDialog(null)}
          onSave={async (reason) => {
            await apiClient.waiveReadiness(finding.finding_id, reason);
            await refresh();
            toast.success(`${finding.criterion.name} is marked not applicable.`);
          }}
        />
      ) : null}
      {dialog === "dismiss" && draft ? (
        <ReasonDialog
          title="Dismiss the drafted issue?"
          description={`The criterion stays missing, and the check never drafts it again for ${finding.scope.name} until someone reopens it.`}
          label="Why"
          confirm="Dismiss the draft"
          onClose={() => setDialog(null)}
          onSave={async (reason) => {
            await apiClient.dismissReadinessDraft(draft.suggestion_id, reason);
            await refresh();
            toast.success("Dismissed: it is not drafted again.");
          }}
        />
      ) : null}
      {dialog === "edit" && draft ? (
        <DraftDialog
          draft={draft.draft}
          onClose={() => setDialog(null)}
          onSave={async (next) => {
            await apiClient.editReadinessDraft(draft.suggestion_id, draft.version, next);
            await refresh();
            toast.success("Draft saved. Create sends it as it reads now.");
          }}
        />
      ) : null}
    </div>
  );
}

function DraftCard({ draft, marker }: { draft: ReadinessDraftDto; marker: string }) {
  return (
    <div className="grid gap-1.5 rounded-2xl border border-grey-border bg-white p-3">
      <p className="text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
        Suggested issue · {draftFacts(draft)}
      </p>
      <p className="text-[14px] font-extrabold text-ink">{draft.summary}</p>
      <p className="whitespace-pre-line text-[13px] text-grey-body">{draft.description}</p>
      <p className="text-[11.5px] text-grey-secondary">
        Created issues also carry the label {marker}, so a retry finds what an earlier try made.
      </p>
    </div>
  );
}

function History({ findingId }: { findingId: string }) {
  const history = useQuery({
    queryKey: ["readiness-history", findingId],
    queryFn: () => apiClient.readinessHistory(findingId),
  });
  return (
    <PanelState
      isLoading={history.isLoading}
      error={history.error}
      isEmpty={history.data?.entries.length === 0}
      emptyText="Nothing recorded yet."
    >
      <ol className="grid gap-1 text-[12.5px]">
        {(history.data?.entries ?? [])
          .slice()
          .reverse()
          .map((entry) => (
            <li key={`${entry.at}-${entry.action}`} className="text-grey-body">
              <span className="text-grey-secondary">
                {shortDay(entry.at)} {formatTime(entry.at)}
              </span>{" "}
              · {actorWords(entry.actor, entry.actor_name)}:{" "}
              {lowerFirst(ACTION_WORDS[entry.action] ?? entry.action)}
              {entry.reason ? ` (${entry.reason})` : ""}
            </li>
          ))}
      </ol>
    </PanelState>
  );
}

function LinkDialog({
  finding,
  onClose,
  onDone,
}: {
  finding: Finding;
  onClose: () => void;
  onDone: () => Promise<unknown>;
}) {
  const [target, setTarget] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const read = linkTarget(target, note);
      if ("problem" in read) throw new Error(read.problem);
      return apiClient.linkReadiness(finding.finding_id, read);
    },
    onSuccess: async () => {
      await onDone();
      toast.success(`Linked: it covers ${finding.criterion.name} for ${finding.scope.name}.`);
      onClose();
    },
    onError: (error) => setProblem(actionError(error)),
  });
  const isRecord = /^https?:/i.test(target.trim());
  return (
    <ReportDialog
      open
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={`Link evidence for ${finding.criterion.name}`}
      description="A Jira issue among the synced ones, or a record outside Jira. It covers the criterion until someone reopens it."
    >
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setProblem(null);
          save.mutate();
        }}
      >
        <div>
          <label htmlFor="readiness-link" className={fieldLabel}>
            Jira key or link
          </label>
          <input
            id="readiness-link"
            className={field}
            value={target}
            placeholder="CHK-12 or https://…"
            onChange={(event) => setTarget(event.target.value)}
          />
        </div>
        {isRecord ? (
          <div>
            <label htmlFor="readiness-note" className={fieldLabel}>
              What it is (optional)
            </label>
            <input
              id="readiness-note"
              className={field}
              value={note}
              maxLength={300}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
        ) : null}
        {problem ? <FormProblem>{problem}</FormProblem> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Linking…" : "Link"}
          </Pill>
        </div>
      </form>
    </ReportDialog>
  );
}

function ReasonDialog({
  title,
  description,
  label,
  confirm,
  onClose,
  onSave,
}: {
  title: string;
  description: string;
  label: string;
  confirm: string;
  onClose: () => void;
  onSave: (reason: string) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => onSave(reason.trim()),
    onSuccess: onClose,
    onError: (error) => setProblem(actionError(error)),
  });
  return (
    <ReportDialog
      open
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={title}
      description={description}
    >
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          const local = reasonProblem(reason);
          setProblem(local);
          if (!local) save.mutate();
        }}
      >
        <div>
          <label htmlFor="readiness-reason" className={fieldLabel}>
            {label}
          </label>
          <textarea
            id="readiness-reason"
            className={cn(field, "h-24 py-2")}
            value={reason}
            maxLength={300}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        {problem ? <FormProblem>{problem}</FormProblem> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : confirm}
          </Pill>
        </div>
      </form>
    </ReportDialog>
  );
}

function DraftDialog({
  draft,
  onClose,
  onSave,
}: {
  draft: ReadinessDraftDto;
  onClose: () => void;
  onSave: (draft: ReadinessDraftDto) => Promise<unknown>;
}) {
  const [next, setNext] = useState({ ...draft, labelsText: (draft.labels ?? []).join(", ") });
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      onSave({
        project_key: next.project_key.trim().toUpperCase(),
        issue_type: next.issue_type.trim(),
        summary: next.summary.trim(),
        description: next.description,
        labels: next.labelsText
          .split(",")
          .map((label) => label.trim())
          .filter(Boolean),
      }),
    onSuccess: onClose,
    onError: (error) => setProblem(actionError(error)),
  });
  const set = (key: keyof typeof next) => (value: string) =>
    setNext((current) => ({ ...current, [key]: value }));
  return (
    <ReportDialog
      open
      wide
      onOpenChange={(open) => (open ? undefined : onClose())}
      title="Edit the drafted issue"
      description="Create sends it as it reads here. OpenProgram adds who approved it and a link back."
    >
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setProblem(null);
          save.mutate();
        }}
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3">
          <TextField
            id="draft-project"
            label="Jira project"
            value={next.project_key}
            onChange={set("project_key")}
          />
          <TextField
            id="draft-type"
            label="Issue type"
            value={next.issue_type}
            onChange={set("issue_type")}
          />
          <TextField
            id="draft-labels"
            label="Labels"
            value={next.labelsText}
            onChange={set("labelsText")}
          />
        </div>
        <TextField
          id="draft-summary"
          label="Summary"
          value={next.summary}
          onChange={set("summary")}
        />
        <div>
          <label htmlFor="draft-text" className={fieldLabel}>
            Text
          </label>
          <textarea
            id="draft-text"
            className={cn(field, "h-40 py-2")}
            value={next.description}
            maxLength={4000}
            onChange={(event) => set("description")(event.target.value)}
          />
        </div>
        {problem ? <FormProblem>{problem}</FormProblem> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save draft"}
          </Pill>
        </div>
      </form>
    </ReportDialog>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className={fieldLabel}>
        {label}
      </label>
      <input
        id={id}
        className={field}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}
