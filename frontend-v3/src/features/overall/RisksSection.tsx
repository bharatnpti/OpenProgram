import type { EscalationMatrixResponse, ProjectRisksResponse } from "../../api/schema";
import { useNames } from "../../app/directory";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity, toneForRag } from "../../lib/status";
import { AGGREGATE_READERS, CONFIG_READERS, useEscalation, useRisks } from "./queries";

const NEEDS = ["fix", "decision", "answer", "review"];

const CONTACT_LABELS: Record<string, string> = {
  team_scrum_master: "Team's scrum master",
  team_manager: "Team's manager",
  member: "Named member",
};

/**
 * Risks and drift from hard signals, worst first, each with what its owner says
 * beside it; then the escalation matrix that decides who an ask reaches when it
 * waits. The Daily report names, per ask, the highest level it reached.
 */
export function RisksSection({ projectId }: { projectId: string }) {
  const risks = useRisks(projectId);
  const escalation = useEscalation(projectId);

  return (
    <section className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <div>
        <SectionHeader
          title="Risks and drift"
          meta="From Jira and Git signals, independent of what anyone reports"
        />
        <PanelState
          locked={risks.locked}
          needs={AGGREGATE_READERS}
          isLoading={risks.query.isLoading}
          error={risks.query.error}
          onRetry={() => void risks.query.refetch()}
          isEmpty={
            risks.query.data
              ? risks.query.data.risks.length + risks.query.data.drift.length === 0
              : false
          }
          emptyText="No open risks or drift for this project."
        >
          {risks.query.data ? <RiskTable data={risks.query.data} /> : null}
        </PanelState>
      </div>

      <div>
        <SectionHeader
          title="Escalation"
          meta="When an ask waits, whom it reaches and after how many days"
        />
        <PanelState
          locked={escalation.locked}
          needs={CONFIG_READERS}
          isLoading={escalation.query.isLoading}
          error={escalation.query.error}
        >
          {escalation.query.data ? <Matrix data={escalation.query.data} /> : null}
        </PanelState>
      </div>
    </section>
  );
}

function RiskTable({ data }: { data: ProjectRisksResponse }) {
  // The API names a risk's owner only sometimes; the directory names the rest,
  // and an id nobody names stays an id.
  const names = useNames();
  const rows = [
    ...data.risks.map((r) => ({
      key: `risk-${r.rule_id}-${r.entity_ref.id}`,
      severity: r.severity,
      what: r.reason,
      ref: r.evidence.identifier,
      kind: r.is_watermelon ? "Watermelon" : "Risk",
      owner: r.person_name ?? names(r.owner_id),
      ownerSays: r.owner_status_summary,
      age: `${r.age_days}d`,
    })),
    ...data.drift.map((d) => ({
      key: `drift-${d.kind}-${d.entity_ref.id}`,
      severity: d.severity,
      what: d.reason,
      ref: d.evidence?.identifier ?? d.entity_ref.id,
      kind: "Drift",
      owner: names(d.owner_id),
      ownerSays: null as string | null,
      age: formatDay(d.detected_at),
    })),
  ].sort((a, b) => ragSeverity(b.severity) - ragSeverity(a.severity));

  return (
    <TableBox>
      <table className="w-full min-w-[820px] border-collapse">
        <thead>
          <tr>
            <th className={th}>Severity</th>
            <th className={th}>Risk</th>
            <th className={th}>Kind</th>
            <th className={th}>Who resolves</th>
            <th className={th}>Owner says</th>
            <th className={th}>Age</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td className={td}>
                <RagChip tone={toneForRag(row.severity)} dot className="h-6 px-2.5 text-[12px]">
                  {row.severity}
                </RagChip>
              </td>
              <td className={td}>
                <span className="font-bold">{row.what}</span>
                <span className="block text-[12px] text-grey-secondary">{row.ref}</span>
              </td>
              <td className={`${td} whitespace-nowrap`}>{row.kind}</td>
              <td className={`${td} whitespace-nowrap`}>{row.owner}</td>
              <td className={`${td} text-grey-body`}>{row.ownerSays ?? "—"}</td>
              <td className={`${td} whitespace-nowrap tabular-nums`}>{row.age}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableBox>
  );
}

function Matrix({ data }: { data: EscalationMatrixResponse }) {
  const names = useNames();
  const source =
    data.source === "project"
      ? "this project's own matrix"
      : "the tenant's matrix (shared default)";
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-2">
      <p className="text-[13px] text-grey-body">
        Using {source}.
        {data.decision_owner_id ? ` Decisions owned by ${names(data.decision_owner_id)}.` : ""}
      </p>
      <TableBox>
        <table className="w-full min-w-[620px] border-collapse">
          <thead>
            <tr>
              <th className={th}>Level</th>
              <th className={th}>Goes to</th>
              {NEEDS.map((need) => (
                <th key={need} className={`${th} text-right`}>
                  {need}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.levels.map((level, i) => (
              <tr key={`${level.label}-${i}`}>
                {/* Level 1 is always the person the ask is for, so the matrix starts at 2,
                    as the backend numbers it (escalation_matrix.py). */}
                <td className={`${td} tabular-nums`}>{i + 2}</td>
                <td className={td}>
                  <span className="font-bold">{level.label}</span>
                  <span className="block text-[12px] text-grey-secondary">
                    {CONTACT_LABELS[level.source] ?? level.source}
                    {level.member_id ? ` · ${names(level.member_id)}` : ""}
                  </span>
                </td>
                {NEEDS.map((need) => (
                  <td key={need} className={`${td} text-right tabular-nums`}>
                    {level.after_days?.[need] !== undefined ? `${level.after_days[need]} d` : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
      <p className="text-[12px] text-grey-secondary">
        Level 1 is the person the ask is for. A dash means that kind of ask never reaches the level.
        A higher level is never reached sooner than the one below it.
      </p>
    </div>
  );
}
