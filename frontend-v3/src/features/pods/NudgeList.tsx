import type { UseQueryResult } from "@tanstack/react-query";

import type { PodCheckinsResponse } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { coverageParts } from "../../lib/observedWords";
import { CellLabel, StackedTable, stackCell, stackRow } from "../coordination/StackedTable";
import { CoverageNoData } from "../today/CoverageNoData";
import { ladderLine, nudgeState, scheduleWords } from "./podWords";

/**
 * Who needs a nudge (D87): for the roles that already read a pod's check-ins (a
 * scrum master for their own pods, a manager, an admin), each person's day as
 * the ladder saw it: replied and when, filled from Jira, or no reply yet, and
 * the rungs that went out (the reminder to them, their scrum master told, their
 * manager told), with times. Check-in time and zone for an admin and the pod's
 * own scrum master only. By name, never ranked, no count across days.
 */
export function NudgeList({
  read,
  day,
}: {
  read: UseQueryResult<PodCheckinsResponse>;
  /** "today", or "on Mon 5 Oct" while a past day is shown. */
  day: string;
}) {
  const board = read.data;
  const people = board?.developers ?? [];
  const schedule = board?.reads_schedule ?? false;
  const { fromJira, noData, fixedInDirectory } = coverageParts(board?.coverage);
  // How many replied is the pod's header line: said once, there.
  return (
    <Panel title="Who needs a nudge">
      <p className="mb-3 text-[13px] text-grey-body">
        Ora sends the reminders. Nothing to chase unless it climbs the ladder.
        {fromJira || noData ? (
          <>
            {" "}
            {fromJira}
            {fromJira && noData ? " · " : ""}
            <CoverageNoData words={noData} fixedInDirectory={fixedInDirectory} />.
          </>
        ) : null}
      </p>
      <PanelState
        isLoading={read.isLoading}
        error={read.error}
        onRetry={() => void read.refetch()}
        isEmpty={people.length === 0}
        emptyText="Nobody in this pod is asked to check in."
      >
        <StackedTable
          caption={`Each person's check-in ${day}, and the reminders that went out`}
          columns={[
            "Person",
            "Check-in",
            "Asked and reminded",
            ...(schedule ? ["Their check-in time"] : []),
          ]}
        >
          {people.map((person) => {
            const state = nudgeState(person, board?.as_of ?? "");
            const ladder = ladderLine(person);
            const time = schedule ? scheduleWords(person) : null;
            return (
              <tr key={person.developer_id} className={stackRow}>
                <td className={`${stackCell} font-bold text-ink`}>{person.developer_name}</td>
                <td className={stackCell}>
                  <CellLabel>Check-in</CellLabel>
                  <RagChip tone={state.tone} className="h-6 px-2.5 text-[12px]">
                    {state.word}
                  </RagChip>
                </td>
                <td className={stackCell}>
                  <CellLabel>Asked and reminded</CellLabel>
                  <span className={state.silent ? "text-ink" : "text-grey-secondary"}>
                    {ladder || "—"}
                  </span>
                </td>
                {schedule ? (
                  <td className={`${stackCell} whitespace-nowrap`}>
                    <CellLabel>Their check-in time</CellLabel>
                    {time ?? "—"}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </StackedTable>
      </PanelState>
    </Panel>
  );
}
