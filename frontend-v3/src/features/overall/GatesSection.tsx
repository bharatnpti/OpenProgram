import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type CSSProperties } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { GateBoardResponse, GateTemplateDto } from "../../api/schema";
import { roleLabels, type AppRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { STAGE_LABELS, stageColor } from "../../components/viz/stages";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { cn } from "../../lib/utils";
import { actionError } from "../reports/access";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { gateCounts, plural, scanSummary } from "./gateWords";
import { failedCells, heatRows, type HeatRow, type HeatState } from "./heat";
import { IssueGatesDialog } from "./IssueGatesDialog";
import { useGateBoard } from "./queries";
import { Fold, Legend, Swatch, VizCard } from "./viz";
import { HATCH } from "./vizStyles";

/** Each gate state's cell: a word and a colour, green, amber and red for the gate state only. */
const CELL: Record<HeatState, { className: string; style?: CSSProperties }> = {
  passed: { className: "bg-rag-green-bg text-rag-green" },
  failed: {
    className: "text-(--op-viz-on-red)",
    style: { background: "var(--op-viz-red-solid)" },
  },
  bypassed: { className: "text-rag-red", style: HATCH },
  open: { className: "bg-rag-amber-bg text-rag-amber" },
  incomplete: { className: "bg-grey-fill font-medium text-grey-body" },
  pending: { className: "bg-grey-fill font-medium text-grey-secondary" },
  na: { className: "font-medium text-grey-secondary" },
};

/**
 * O5, "Acceptance gates" as a heatmap: each requirement against each gate,
 * furthest stage first, in a real table. The rows where nothing has started
 * fold under "Show all"; what each gate checks folds too. A requirement opens
 * its gate dialog, where items are kept, dismissed, added and signed off.
 * Everyone but an executive rereads Jira here. It replaces the per-requirement
 * gate columns and the Flag column, and the always-open gate definitions.
 */
export function GatesSection({
  projectId,
  releaseId = "",
  summary = true,
}: {
  projectId: string;
  releaseId?: string;
  /** The count chips; a developer's answer line already says them. */
  summary?: boolean;
}) {
  const { editGates, pastDay } = useReportAccess();
  const offNow = pastDay("editGates");
  const queryClient = useQueryClient();
  const { query } = useGateBoard(projectId, releaseId || undefined);
  const data = query.data;
  const templates = (data?.templates ?? []).filter((t) => t.enabled);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const scan = useMutation({
    mutationFn: () => apiClient.scanGates(projectId, releaseId || undefined),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["gates", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
      ]);
      toast.success(scanSummary(result));
    },
    onError: (error) => toast.error(actionError(error)),
  });

  return (
    <section
      aria-labelledby="o5-h"
      // Drawn as it nears the viewport, like the flow above it.
      className="[contain-intrinsic-size:auto_640px] [content-visibility:auto]"
    >
      <SectionHeader
        id="o5-h"
        title="Acceptance gates"
        meta="What must be true before a requirement moves on, and who says so"
        actions={
          editGates ? (
            <Pill size="sm" variant="ghost" disabled={scan.isPending} onClick={() => scan.mutate()}>
              {scan.isPending ? "Reading Jira…" : "Read Jira now"}
            </Pill>
          ) : offNow ? (
            <Locked>{offNow}</Locked>
          ) : undefined
        }
      />
      <PanelState
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={templates.length === 0}
        emptyText="No gates are switched on. An admin defines them on the console's Admin → Gates tab."
      >
        {data ? (
          <VizCard>
            {data.issues.length === 0 ? (
              <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
                No requirements in this scope yet.
              </p>
            ) : (
              <Heatmap
                data={data}
                templates={templates}
                summary={summary}
                canKeep={editGates}
                onOpen={setOpenKey}
              />
            )}
            <Fold summary="What each gate checks">
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-x-5 gap-y-3 text-[13px] text-grey-body">
                {templates.map((template) => (
                  <GateDefinition key={template.template_id} template={template} />
                ))}
              </div>
              <p className="mt-2.5 text-[12.5px] text-grey-secondary">
                A gate does not block Jira. A requirement that moves on without passing is flagged
                so the gap is visible. Items read from Jira count once someone keeps them.
              </p>
            </Fold>
          </VizCard>
        ) : null}
      </PanelState>
      {openKey && data ? (
        <IssueGatesDialog
          board={data}
          issueKey={openKey}
          projectId={projectId}
          onClose={() => setOpenKey(null)}
        />
      ) : null}
    </section>
  );
}

function Heatmap({
  data,
  templates,
  summary,
  canKeep,
  onOpen,
}: {
  data: GateBoardResponse;
  templates: GateTemplateDto[];
  summary: boolean;
  canKeep: boolean;
  onOpen: (key: string) => void;
}) {
  const { shown, folded } = heatRows(data.issues, templates);
  const counts = gateCounts(data);
  const failed = failedCells([...shown, ...folded]);
  const states = new Set([...shown, ...folded].flatMap((row) => row.cells.map((c) => c.state)));
  return (
    <>
      {summary ? (
        <div className="flex flex-wrap gap-1.5">
          <RagChip tone="neutral" className="h-[22px] px-2 text-[11.5px]">
            {counts.passedAll} of {counts.issues} passed every gate
          </RagChip>
          {counts.passedWithout > 0 ? (
            <RagChip tone="danger" className="h-[22px] px-2 text-[11.5px]">
              {counts.passedWithout} moved on without passing
            </RagChip>
          ) : null}
          {counts.suggestions > 0 ? (
            <RagChip tone="info" className="h-[22px] px-2 text-[11.5px]">
              {plural(counts.suggestions, "suggestion")} from Jira
              {canKeep ? " to keep or dismiss" : ", not kept"}
            </RagChip>
          ) : null}
        </div>
      ) : null}
      {shown.length > 0 ? (
        <HeatTable
          rows={shown}
          templates={templates}
          caption={`Gate states for the ${plural(shown.length, "requirement")} that need attention, furthest stage first`}
          onOpen={onOpen}
        />
      ) : (
        <p className="text-[13.5px] text-grey-body">
          None of the {plural(folded.length, "requirement")} has started a gate yet.
        </p>
      )}
      <Legend>
        <span>
          <Swatch className="bg-rag-green-bg shadow-[inset_0_0_0_1px_var(--op-viz-axis)]" />
          Passed
        </span>
        <span>
          <Swatch className="bg-grey-fill shadow-[inset_0_0_0_1px_var(--op-viz-axis)]" />
          Not started
        </span>
        <span>
          <Swatch style={{ ...HATCH, boxShadow: "inset 0 0 0 1px var(--op-viz-axis)" }} />
          Bypassed: moved on without passing
        </span>
        <span>
          <Swatch style={{ background: "var(--op-viz-red-solid)" }} />
          Failed{failed === 0 ? " (none yet)" : ""}
        </span>
        {states.has("open") ? (
          <span>
            <Swatch className="bg-rag-amber-bg shadow-[inset_0_0_0_1px_var(--op-viz-axis)]" />
            Open: kept items still to check
          </span>
        ) : null}
        {states.has("incomplete") ? (
          <span>
            <Swatch className="bg-grey-fill shadow-[inset_0_0_0_1px_var(--op-viz-axis)]" />
            Incomplete: a kind of item not kept yet
          </span>
        ) : null}
        {canKeep ? (
          <span className="text-grey-secondary">
            Open a requirement to keep or dismiss what Jira suggests
          </span>
        ) : null}
      </Legend>
      {folded.length > 0 && shown.length > 0 ? (
        <Fold
          summary={
            <>
              Show all {shown.length + folded.length}
              <span className="font-medium text-grey-secondary">
                · {folded.length} more, none started
              </span>
            </>
          }
        >
          <HeatTable
            rows={folded}
            templates={templates}
            caption={`Gate states for the other ${plural(folded.length, "requirement")}`}
            onOpen={onOpen}
          />
        </Fold>
      ) : folded.length > 0 ? (
        <Fold summary={`Show all ${folded.length}`}>
          <HeatTable
            rows={folded}
            templates={templates}
            caption={`Gate states for all ${plural(folded.length, "requirement")}`}
            onOpen={onOpen}
          />
        </Fold>
      ) : null}
    </>
  );
}

/**
 * The heatmap as a table: requirement and stage in row headers, one column per
 * gate. On a phone the stage moves under the requirement and its column goes.
 */
function HeatTable({
  rows,
  templates,
  caption,
  onOpen,
}: {
  rows: HeatRow[];
  templates: GateTemplateDto[];
  caption: string;
  onOpen: (key: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="-m-1 w-[calc(100%+8px)] table-fixed border-separate border-spacing-1 text-[12.5px]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th
              scope="col"
              className="w-[40%] px-1 pb-1 text-left align-bottom text-[11px] font-bold uppercase tracking-wide text-grey-secondary sm:w-[44%]"
            >
              Requirement
            </th>
            <th
              scope="col"
              className="hidden px-1 pb-1 text-left align-bottom text-[11px] font-bold uppercase tracking-wide text-grey-secondary sm:table-cell"
            >
              Stage
            </th>
            {templates.map((t) => (
              <th
                key={t.template_id}
                scope="col"
                className="px-1 pb-1 text-left align-bottom text-[11px] font-bold uppercase tracking-wide text-grey-secondary"
              >
                {t.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ issue, cells }) => (
            <tr key={issue.key}>
              <th scope="row" className="min-w-0 px-1 py-0.5 text-left font-bold">
                <button
                  type="button"
                  className="block w-full text-left hover:underline"
                  onClick={() => onOpen(issue.key)}
                >
                  <span className="block text-[13px] font-extrabold text-ink">{issue.key}</span>
                  <span className="block text-[12px] font-medium text-grey-body">
                    {issue.title}
                  </span>
                  <StageWords stage={issue.stage} className="sm:hidden" />
                </button>
              </th>
              <td className="hidden px-1 sm:table-cell">
                <StageWords stage={issue.stage} />
              </td>
              {cells.map((cell, index) => (
                <td key={templates[index].template_id} className="p-0">
                  <span
                    className={cn(
                      "flex min-h-10 flex-col items-center justify-center gap-0.5 rounded-lg px-1.5 py-1 text-center font-bold",
                      CELL[cell.state].className,
                    )}
                    style={CELL[cell.state].style}
                  >
                    {cell.label}
                    {cell.note ? (
                      <small className="text-[11px] font-bold">{cell.note}</small>
                    ) : null}
                  </span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StageWords({ stage, className }: { stage: IssueStage; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-[11.5px] font-medium text-grey-secondary",
        className,
      )}
    >
      <Swatch className="h-2 w-2 rounded-[2px]" style={{ background: stageColor(stage) }} />
      {STAGE_LABELS[stage]}
    </span>
  );
}

type IssueStage = GateBoardResponse["issues"][number]["stage"];

function GateDefinition({ template }: { template: GateTemplateDto }) {
  return (
    <div className="border-l-[3px] border-magenta pl-3">
      <b className="block text-[14px] text-ink">{template.name}</b>
      Checked before {STAGE_LABELS[template.guards_stage].toLowerCase()}
      {template.issue_types && template.issue_types.length > 0
        ? `, ${template.issue_types.join(", ")} only`
        : ""}
      .{" "}
      {template.kinds.map((kind) => (
        <span key={kind.key}>
          {kind.label}: signed off by {listRoles(kind.sign_off_roles)}
          {kind.evidence_required ? ", met only with a link to the evidence" : ""}.
          {kind.headings && kind.headings.length > 0
            ? ` Read from Jira under “${kind.headings.join("”, “")}”${kind.gherkin ? ", including Given / When / Then" : ""}.`
            : ""}{" "}
        </span>
      ))}
    </div>
  );
}

function listRoles(roles: string[]): string {
  const names = roles.map((r) => (r in roleLabels ? roleLabels[r as AppRole] : r).toLowerCase());
  if (names.length === 0) return "an admin";
  if (names.length === 1) return `the ${names[0]}`;
  return `the ${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}
