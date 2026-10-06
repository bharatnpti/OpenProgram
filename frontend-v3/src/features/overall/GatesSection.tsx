import type {
  GateBoardResponse,
  GateEvaluationResponse,
  GateState,
  GateTemplateDto,
} from "../../api/schema";
import { roleLabels, type AppRole } from "../../app/role";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { STAGE_LABELS } from "../../lib/status";
import { useGateBoard } from "./queries";

const STATE_LABELS: Record<GateState, string> = {
  passed: "Passed",
  open: "Open",
  failed: "Failed",
  missing: "Nothing kept yet",
};

const STATE_TONES: Record<GateState, BadgeTone> = {
  passed: "success",
  open: "warning",
  failed: "danger",
  missing: "neutral",
};

/**
 * The checks every requirement passes on its way to production, in plain words,
 * then each requirement against each check. A tenant starts with two: business
 * acceptance before production, engineering delivery (test cases) before
 * business testing. Admins define them on the console's Admin → Gates tab.
 */
export function GatesSection({ projectId }: { projectId: string }) {
  const { query } = useGateBoard(projectId);
  const data = query.data;
  const templates = (data?.templates ?? []).filter((t) => t.enabled);

  return (
    <section>
      <SectionHeader
        title="Acceptance gates"
        meta="What must be true before a requirement moves on, and who says so"
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
              below so the gap is visible.
            </p>
            <GateTable data={data} templates={templates} />
          </div>
        ) : null}
      </PanelState>
    </section>
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

function GateTable({ data, templates }: { data: GateBoardResponse; templates: GateTemplateDto[] }) {
  // `passed_without` carries gate names (not ids), as the server words them.
  return (
    <TableBox>
      <table className="w-full min-w-[760px] border-collapse">
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
                <span className="font-bold">{issue.key}</span> {issue.title}
              </td>
              <td className={`${td} whitespace-nowrap`}>{STAGE_LABELS[issue.stage]}</td>
              {templates.map((t) => (
                <td key={t.template_id} className={td}>
                  <Evaluation
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

function Evaluation({ evaluation }: { evaluation: GateEvaluationResponse | undefined }) {
  if (!evaluation) return <span className="text-grey-secondary">Does not apply</span>;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-1">
      <RagChip tone={STATE_TONES[evaluation.state]} className="h-6 w-fit px-2.5 text-[12px]">
        {STATE_LABELS[evaluation.state]}
      </RagChip>
      <span className="text-[12px] text-grey-body">
        {evaluation.total > 0 ? `${evaluation.met} of ${evaluation.total} met` : "none kept"}
        {evaluation.suggested > 0 ? ` · ${evaluation.suggested} suggested from Jira` : ""}
        {evaluation.missing_kinds.length > 0
          ? ` · missing ${evaluation.missing_kinds.join(", ")}`
          : ""}
      </span>
    </div>
  );
}
