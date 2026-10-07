import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { GateBoardResponse, GateEvaluationResponse, GateTemplateDto } from "../../api/schema";
import { roleLabels, type AppRole } from "../../app/role";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { STAGE_LABELS } from "../../lib/status";
import { WHO, actionError } from "../reports/access";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import {
  GATE_STATE_TONES,
  evaluationChip,
  evaluationLine,
  gateCounts,
  needsSomeone,
  plural,
  scanSummary,
} from "./gateWords";
import { IssueGatesDialog } from "./IssueGatesDialog";
import { useGateBoard } from "./queries";

/**
 * The checks every requirement passes on its way to production, in plain words,
 * then each requirement against each check. A tenant starts with two: business
 * acceptance before production, engineering delivery (test cases) before
 * business testing. Admins define them on the console's Admin → Gates tab.
 * Everyone but an executive keeps or dismisses what Jira suggests, adds items
 * and rereads Jira; only the roles a gate names sign its items off.
 */
export function GatesSection({
  projectId,
  releaseId = "",
}: {
  projectId: string;
  releaseId?: string;
}) {
  const { editGates, why } = useReportAccess();
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
    <section>
      <SectionHeader
        title="Acceptance gates"
        meta="What must be true before a requirement moves on, and who says so"
        actions={
          editGates ? (
            <Pill size="sm" variant="ghost" disabled={scan.isPending} onClick={() => scan.mutate()}>
              {scan.isPending ? "Reading Jira…" : "Read Jira now"}
            </Pill>
          ) : (
            <Locked>
              {why("editGates", `Rereading Jira and keeping items is for ${WHO.gates}.`)}
            </Locked>
          )
        }
      />
      <PanelState
        needs="anyone but an executive, or a project-progress reader"
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={templates.length === 0}
        emptyText="No gates are switched on. An admin defines them on the console's Admin → Gates tab."
      >
        {data ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2">
              {templates.map((template) => (
                <GateDefinition key={template.template_id} template={template} />
              ))}
            </ul>
            <p className="text-[12px] text-grey-secondary">
              A gate does not block Jira. When a requirement moves on without passing, it is flagged
              below so the gap is visible. Items read from Jira count once someone keeps them.
            </p>
            {data.issues.length === 0 ? (
              <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
                No requirements in this scope yet.
              </p>
            ) : (
              <>
                <Counts data={data} />
                <GateTable data={data} templates={templates} onOpen={setOpenKey} />
              </>
            )}
          </div>
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

function Counts({ data }: { data: GateBoardResponse }) {
  const counts = gateCounts(data);
  const waiting = data.issues.filter(needsSomeone).length;
  return (
    <p className="text-[13px] text-grey-body">
      {counts.passedAll} of {counts.issues} passed every gate
      {counts.passedWithout > 0 ? ` · ${counts.passedWithout} moved on without passing` : ""}
      {counts.suggestions > 0
        ? ` · ${plural(counts.suggestions, "suggestion")} from Jira to keep or dismiss`
        : ""}
      {waiting > 0
        ? ` · ${plural(waiting, "issue")} ${waiting === 1 ? "needs" : "need"} someone's attention`
        : ""}
    </p>
  );
}

function GateDefinition({ template }: { template: GateTemplateDto }) {
  return (
    <li className="border-l-[3px] border-magenta py-1 pl-4">
      <h3 className="text-[16px] font-extrabold">{template.name}</h3>
      <p className="mt-1 text-[13px] font-bold">
        Checked before {STAGE_LABELS[template.guards_stage].toLowerCase()}
        {template.issue_types && template.issue_types.length > 0
          ? ` · ${template.issue_types.join(", ")} only`
          : ""}
      </p>
      <ul className="mt-2 grid gap-1.5 text-[13px] text-grey-body">
        {template.kinds.map((kind) => (
          <li key={kind.key}>
            <span className="font-bold text-ink">{kind.label}</span>: signed off by{" "}
            {listRoles(kind.sign_off_roles)}
            {kind.evidence_required ? ", met only with a link to the evidence" : ""}.
            {kind.headings && kind.headings.length > 0
              ? ` Read from Jira under “${kind.headings.join("”, “")}”${kind.gherkin ? ", including Given / When / Then" : ""}.`
              : ""}
          </li>
        ))}
      </ul>
    </li>
  );
}

function listRoles(roles: string[]): string {
  const names = roles.map((r) => (r in roleLabels ? roleLabels[r as AppRole] : r).toLowerCase());
  if (names.length === 0) return "an admin";
  if (names.length === 1) return `the ${names[0]}`;
  return `the ${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

function GateTable({
  data,
  templates,
  onOpen,
}: {
  data: GateBoardResponse;
  templates: GateTemplateDto[];
  onOpen: (key: string) => void;
}) {
  // `passed_without` carries gate names (not ids), as the server words them.
  return (
    <TableBox>
      <table className="w-full min-w-[560px] sm:min-w-[760px] border-collapse">
        <thead>
          <tr>
            <th className={th}>Requirement</th>
            <th className={th}>Stage</th>
            {templates.map((t) => (
              <th key={t.template_id} className={th}>
                {t.name}
              </th>
            ))}
            <th className={th}>Flag</th>
          </tr>
        </thead>
        <tbody>
          {data.issues.map((issue) => (
            <tr key={issue.key}>
              <td className={td}>
                {/* Capped on a phone, so the stage beside it is in the first screenful. */}
                <div className="max-w-[14rem] sm:max-w-none">
                  <button
                    type="button"
                    className="text-left hover:underline"
                    onClick={() => onOpen(issue.key)}
                  >
                    <span className="font-bold">{issue.key}</span> {issue.title}
                  </button>
                  <span className="block text-[12px] text-grey-secondary">
                    {issue.items.some((item) => item.status === "suggested")
                      ? "Suggestions to keep or dismiss"
                      : "Open to see its items"}
                  </span>
                </div>
              </td>
              <td className={`${td} whitespace-nowrap`}>{STAGE_LABELS[issue.stage]}</td>
              {templates.map((t) => (
                <td key={t.template_id} className={td}>
                  <Evaluation
                    template={t}
                    evaluation={issue.evaluations.find((e) => e.template_id === t.template_id)}
                  />
                </td>
              ))}
              <td className={td}>
                {issue.passed_without.length > 0 ? (
                  <span className="text-[12px] font-bold text-rag-red">
                    Moved on without passing {issue.passed_without.join(", ")}
                  </span>
                ) : (
                  <span className="text-grey-secondary">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableBox>
  );
}

function Evaluation({
  template,
  evaluation,
}: {
  template: GateTemplateDto;
  evaluation: GateEvaluationResponse | undefined;
}) {
  if (!evaluation) return <span className="text-grey-secondary">Does not apply</span>;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-1">
      <RagChip tone={GATE_STATE_TONES[evaluation.state]} className="h-6 w-fit px-2.5 text-[12px]">
        {evaluationChip(evaluation)}
      </RagChip>
      <span className="text-[12px] text-grey-body">{evaluationLine(evaluation, template)}</span>
    </div>
  );
}
