import { Link } from "react-router-dom";

import type { ProjectRisksResponse } from "../../api/schema";
import { useNames } from "../../app/directory";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity, toneForRag } from "../../lib/status";
import { useRisks } from "./queries";

/**
 * Risks and drift from hard signals, worst first, each with what its owner says
 * beside it. The escalation matrix that decides who an ask reaches is kept on
 * Admin › Escalation; an admin gets a link to it here.
 */
export function RisksSection({ projectId }: { projectId: string }) {
  const risks = useRisks(projectId);
  const { access } = useRole();

  return (
    <section>
      <SectionHeader
        title="Risks and drift"
        meta="From Jira and Git signals, independent of what anyone reports"
        actions={
          access.overall.escalationLink ? (
            <Link
              to="/admin?tab=escalation"
              className="text-[13px] font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
            >
              Escalation settings
            </Link>
          ) : undefined
        }
      />
      <PanelState
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
    </section>
  );
}

function RiskTable({ data }: { data: ProjectRisksResponse }) {
  // The API names a risk's owner only sometimes; the directory names the rest,
  // and an id nobody names stays an id.
  const names = useNames();
  const rows = [
    ...data.risks.map((r) => ({
      // A person can have several merge requests open: the request makes the finding its own.
      key: `risk-${r.rule_id}-${r.entity_ref.id}-${r.evidence.identifier}`,
      severity: r.severity,
      what: r.reason,
      ref: r.evidence.identifier,
      kind: r.is_watermelon ? "Watermelon" : "Risk",
      owner: r.person_name ?? names(r.owner_id),
      ownerSays: r.owner_status_summary,
      age: `${r.age_days}d`,
    })),
    ...data.drift.map((d) => ({
      key: `drift-${d.kind}-${d.entity_ref.id}-${d.evidence?.identifier ?? ""}`,
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
      <table className="w-full min-w-[600px] sm:min-w-[820px] border-collapse">
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
                <div className="max-w-[16rem] sm:max-w-none">
                  <span className="font-bold">{row.what}</span>
                  <span className="block text-[12px] text-grey-secondary">{row.ref}</span>
                </div>
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
