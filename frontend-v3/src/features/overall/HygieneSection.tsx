import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  HygieneBoardResponse,
  HygieneFindingResponse,
  HygieneRuleRowDto,
} from "../../api/schema";
import { useRole } from "../../app/role";
import { usePastDayLine, useReadOnly } from "../../app/viewingDate";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { STAGE_LABELS } from "../../components/viz/stages";
import { formatDay, formatTime } from "../../lib/format";
import { cn } from "../../lib/utils";
import { actionError } from "../reports/access";
import { FormProblem, Locked, ReportDialog, field, fieldLabel } from "../reports/ReportDialog";
import {
  answerLine,
  decisionWords,
  findingsOf,
  footerWords,
  hasSection,
  modeWords,
  openWords,
  ownerWords,
  reasonProblem,
  rowCounts,
} from "./hygieneWords";
import { useHygiene } from "./queries";
import { VizCard } from "./viz";

type Finding = HygieneFindingResponse;

/** Every read an action moves: the board and today's report. */
function useRefresh(projectId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["hygiene", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
    ]);
}

/**
 * Jira hygiene on Overall (`#hygiene`): what is missing or wrong on the project's
 * Jira issues, by the rules an admin set, checked every hour. The rule is stated
 * first; one row per rule with its counts; a row folds out its issues, each with
 * what is wrong, who is asked, the suggested fix and the actions the server says
 * this person has. An executive reads the counts and never who. A filter keeps
 * one pod or release. A tenant that has not switched the scan on gets no section.
 * Nothing here writes to Jira.
 */
export function HygieneSection({
  projectId,
  releaseId = "",
}: {
  projectId: string;
  releaseId?: string;
}) {
  const { access } = useRole();
  const { readOnly } = useReadOnly();
  const pastDay = usePastDayLine();
  const [podId, setPodId] = useState("");
  const { readable, query } = useHygiene(projectId, releaseId || undefined, podId || undefined);
  const refresh = useRefresh(projectId);
  const run = useMutation({
    mutationFn: () =>
      apiClient.runHygiene(projectId, {
        releaseId: releaseId || undefined,
        podId: podId || undefined,
      }),
    onSuccess: async (result) => {
      await refresh();
      const { opened, resolved, open } = result.run;
      toast.success(
        `Checked: ${open} open${opened ? `, ${opened} new` : ""}${resolved ? `, ${resolved} fixed` : ""}.`,
      );
    },
    onError: (error) => toast.error(actionError(error)),
  });
  if (!readable) return null;
  const data = query.data;
  if (data && !hasSection(data)) return null;

  return (
    <section
      id="hygiene"
      aria-labelledby="hygiene-h"
      className="scroll-mt-24 [contain-intrinsic-size:auto_420px] [content-visibility:auto]"
    >
      <SectionHeader
        id="hygiene-h"
        title="Jira hygiene"
        meta={`What is missing or wrong on ${releaseId ? "this release's" : "the project's"} Jira issues, by the rules your admin set. Checked every hour; nothing is written to Jira.`}
        actions={
          data?.can_run && !readOnly ? (
            <Pill size="sm" variant="ghost" disabled={run.isPending} onClick={() => run.mutate()}>
              {run.isPending ? "Checking…" : "Check now"}
            </Pill>
          ) : data?.can_run && pastDay ? (
            <Locked>{pastDay}</Locked>
          ) : undefined
        }
      />
      <PanelState
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={data !== undefined && data.rules.length === 0}
        emptyText="No rule is on yet. An admin adds them under Admin, Jira hygiene."
      >
        {data ? (
          <Board
            data={data}
            projectId={projectId}
            podId={podId}
            onPod={setPodId}
            canAct={access.hygiene.act && !readOnly}
          />
        ) : null}
      </PanelState>
    </section>
  );
}

function Board({
  data,
  projectId,
  podId,
  onPod,
  canAct,
}: {
  data: HygieneBoardResponse;
  projectId: string;
  podId: string;
  onPod: (podId: string) => void;
  canAct: boolean;
}) {
  return (
    <VizCard className="gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h3 className="min-w-0 text-[17px] font-extrabold leading-snug text-balance">
          {answerLine(data)}
        </h3>
        {data.pods.length > 1 && !data.counts_only ? (
          <label className="flex items-center gap-2 text-[13px] font-bold">
            <span>Pod</span>
            <select
              className="h-9 max-w-[220px] rounded-full border border-grey-border bg-white px-3 text-[13px]"
              value={podId}
              onChange={(event) => onPod(event.target.value)}
            >
              <option value="">Every pod</option>
              {data.pods.map((pod) => (
                <option key={pod.id} value={pod.id}>
                  {pod.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <ul className="grid gap-2" aria-label="Rules">
        {data.rules.map((row) => (
          <RuleItem key={row.rule_id} row={row} data={data} projectId={projectId} canAct={canAct} />
        ))}
      </ul>
      {data.counts_only ? (
        <p className="text-[12px] text-grey-secondary">
          Counts per rule. The issues and who is asked are for the people who act on them.
        </p>
      ) : null}
      <p className="text-[12px] text-grey-secondary">{footerWords(data.scan, data.held, moment)}</p>
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

function RuleItem({
  row,
  data,
  projectId,
  canAct,
}: {
  row: HygieneRuleRowDto;
  data: HygieneBoardResponse;
  projectId: string;
  canAct: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { open: openFindings, decided } = findingsOf(data.findings, row.rule_id);
  const listId = `hygiene-${row.rule_id}`;
  const foldable = !data.counts_only && openFindings.length + decided.length > 0;
  return (
    <li className="rounded-2xl border border-grey-border">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 px-4 py-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-extrabold text-ink">{row.name}</span>
            <RagChip
              tone={row.severity === "ask" ? "warning" : "neutral"}
              className="h-6 px-2.5 text-[11.5px]"
            >
              {modeWords(row)}
            </RagChip>
          </p>
          <p className="mt-0.5 text-[12.5px] text-grey-body">{row.stated}</p>
        </div>
        <div className="flex flex-none flex-col items-end gap-1 max-sm:w-full max-sm:flex-row max-sm:items-center max-sm:justify-between">
          <span
            className={cn(
              "text-[13px] font-bold",
              row.open > 0 ? "text-ink" : "text-grey-secondary",
            )}
          >
            {rowCounts(row)}
          </span>
          {foldable ? (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={listId}
              className="min-h-9 text-[12.5px] font-bold text-magenta underline-offset-2 hover:underline"
              onClick={() => setOpen((value) => !value)}
            >
              {open ? "Hide the issues" : "Show the issues"}
            </button>
          ) : null}
        </div>
      </div>
      {open && foldable ? (
        <div id={listId} className="border-t border-grey-border px-4 py-3">
          <ul className="grid gap-3">
            {openFindings.map((finding) => (
              <FindingItem
                key={finding.finding_id}
                finding={finding}
                projectId={projectId}
                canAct={canAct}
              />
            ))}
          </ul>
          {decided.length > 0 ? (
            <details className="mt-3">
              <summary className="cursor-pointer text-[12.5px] font-bold text-grey-body">
                Waived or set aside ({decided.length})
              </summary>
              <ul className="mt-2 grid gap-3">
                {decided.map((finding) => (
                  <FindingItem
                    key={finding.finding_id}
                    finding={finding}
                    projectId={projectId}
                    canAct={canAct}
                  />
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function FindingItem({
  finding,
  projectId,
  canAct,
}: {
  finding: Finding;
  projectId: string;
  canAct: boolean;
}) {
  const refresh = useRefresh(projectId);
  const [dialog, setDialog] = useState<"waive" | "aside" | null>(null);
  const can = canAct ? finding.can : null;
  const act = useMutation({
    mutationFn: (step: () => Promise<unknown>) => step(),
    onSuccess: async () => {
      await refresh();
    },
    onError: (error) => toast.error(actionError(error)),
  });
  const decided = decisionWords(finding);
  return (
    <li className="grid gap-1 text-[13px]">
      <p className="min-w-0">
        <b className="text-ink">{finding.issue_key}</b>{" "}
        <span className="text-ink">{finding.title}</span>
        {finding.stage ? (
          <span className="text-grey-secondary"> · {STAGE_LABELS[finding.stage]}</span>
        ) : null}
        {finding.scope.kind !== "project" ? (
          <span className="text-grey-secondary"> · {finding.scope.name}</span>
        ) : null}
      </p>
      <p className="text-grey-body">
        {finding.what}. <span className="text-grey-secondary">{openWords(finding)}.</span>
        {finding.held ? (
          <span className="text-grey-secondary"> Read while the Jira sync was behind.</span>
        ) : null}
      </p>
      <p className="text-grey-body">
        <b className="text-ink">Asked of:</b> {ownerWords(finding.owner)}
      </p>
      <p className="text-grey-body">
        <b className="text-ink">Suggested fix:</b> {finding.suggested_fix}
      </p>
      {decided ? <p className="font-bold text-ink">{decided}</p> : null}
      {can && (can.waive || can.not_applicable || can.recheck || can.reopen) ? (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          {can.recheck ? (
            <Pill
              size="sm"
              variant="ghost"
              className="h-8 px-3"
              disabled={act.isPending}
              onClick={() =>
                act.mutate(async () => {
                  const result = await apiClient.recheckHygiene(finding.finding_id);
                  toast.success(
                    result.state === "resolved"
                      ? `${finding.issue_key} is fixed in Jira now.`
                      : `${finding.issue_key} still breaks the rule in Jira's last sync.`,
                  );
                })
              }
            >
              Re-check
            </Pill>
          ) : null}
          {can.waive ? (
            <Pill size="sm" variant="ghost" className="h-8 px-3" onClick={() => setDialog("waive")}>
              Waive…
            </Pill>
          ) : null}
          {can.not_applicable ? (
            <Pill size="sm" variant="ghost" className="h-8 px-3" onClick={() => setDialog("aside")}>
              Not applicable…
            </Pill>
          ) : null}
          {can.reopen ? (
            <Pill
              size="sm"
              variant="ghost"
              className="h-8 px-3"
              disabled={act.isPending}
              onClick={() =>
                act.mutate(async () => {
                  await apiClient.reopenHygiene(finding.finding_id);
                  toast.success(`${finding.issue_key} is checked again from the next run.`);
                })
              }
            >
              Reopen
            </Pill>
          ) : null}
        </div>
      ) : null}
      {dialog ? (
        <ReasonDialog
          title={
            dialog === "waive"
              ? `Waive ${finding.issue_key}?`
              : `${finding.issue_key}: not applicable?`
          }
          description={
            dialog === "waive"
              ? "It stays listed as waived and asks nobody while the rule still finds it."
              : "The rule does not apply to this issue. It stays listed as set aside."
          }
          confirm={dialog === "waive" ? "Waive" : "Set aside"}
          onClose={() => setDialog(null)}
          onSave={async (text) => {
            if (dialog === "waive") await apiClient.waiveHygiene(finding.finding_id, text);
            else await apiClient.setAsideHygiene(finding.finding_id, text);
            await refresh();
            toast.success(
              dialog === "waive"
                ? `${finding.issue_key} waived.`
                : `${finding.issue_key} set aside as not applicable.`,
            );
          }}
        />
      ) : null}
    </li>
  );
}

function ReasonDialog({
  title,
  description,
  confirm,
  onClose,
  onSave,
}: {
  title: string;
  description: string;
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
          <label htmlFor="hygiene-reason" className={fieldLabel}>
            Why
          </label>
          <textarea
            id="hygiene-reason"
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
