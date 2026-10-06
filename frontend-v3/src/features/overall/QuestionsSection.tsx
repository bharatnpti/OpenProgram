import type { QuestionStatus } from "../../api/schema";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import type { BadgeTone } from "../../lib/status";
import { useGateBoard } from "./queries";

const STATUS_LABELS: Record<QuestionStatus, string> = {
  not_yet: "Not yet",
  partly: "Partly",
  answered: "Yes",
  closed_unanswered: "Closed without an answer",
};

const STATUS_TONES: Record<QuestionStatus, BadgeTone> = {
  not_yet: "warning",
  partly: "info",
  answered: "success",
  closed_unanswered: "neutral",
};

/** Every question kept from Jira comments: what was asked, of whom, and whether they answered. */
export function QuestionsSection({ projectId }: { projectId: string }) {
  const { query } = useGateBoard(projectId);
  const questions = query.data?.questions ?? [];
  const open = questions.filter((q) => q.status === "not_yet").length;

  return (
    <section>
      <SectionHeader
        title="What we asked, and what we heard back"
        meta={
          query.data
            ? `${questions.length} questions kept from Jira · ${open} not answered yet`
            : undefined
        }
      />
      <PanelState
        needs="anyone but an executive, or a project-progress reader"
        isLoading={query.isLoading}
        error={query.error}
        isEmpty={questions.length === 0}
        emptyText="No questions kept yet. A Jira comment that mentions someone and asks, or starts with “Question:”, is suggested here."
      >
        <TableBox>
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr>
                <th className={th}>Ticket</th>
                <th className={th}>What we asked</th>
                <th className={th}>Asked of</th>
                <th className={th}>Asked by</th>
                <th className={th}>When</th>
                <th className={th}>Heard back?</th>
              </tr>
            </thead>
            <tbody>
              {[...questions]
                .sort((a, b) => b.asked_at.localeCompare(a.asked_at))
                .map((q) => (
                  <tr key={q.question_id}>
                    <td className={`${td} whitespace-nowrap font-bold`}>{q.issue_key}</td>
                    <td className={td}>
                      {q.summary}
                      {!q.confirmed ? (
                        <span className="ml-1 text-[11px] text-grey-secondary">suggested</span>
                      ) : null}
                    </td>
                    <td className={`${td} whitespace-nowrap`}>{q.asked_to_name}</td>
                    <td className={`${td} whitespace-nowrap`}>{q.asked_by_name}</td>
                    <td className={`${td} whitespace-nowrap`}>{formatDay(q.asked_at)}</td>
                    <td className={td}>
                      <RagChip tone={STATUS_TONES[q.status]} className="h-6 px-2.5 text-[12px]">
                        {STATUS_LABELS[q.status]}
                      </RagChip>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </TableBox>
      </PanelState>
    </section>
  );
}
