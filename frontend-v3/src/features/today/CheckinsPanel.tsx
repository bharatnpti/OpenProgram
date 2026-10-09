import type { UseQueryResult } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";

import type { PodCheckinsResponse } from "../../api/schema";
import { useRememberedOpen } from "../../app/rememberedOpen";
import { PanelState } from "../../components/PanelState";
import { Row } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import {
  boardMeta,
  boardRag,
  boardWord,
  checkinsSummary,
  followUpWords,
} from "../../lib/checkinWords";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";

const LIST_ID = "sm-checkins-list";

/**
 * A scrum master's check-ins for the pod, beside the pod's dates and work:
 * folded by default to one line ("4 of 6 replied today"), with a red flag
 * while anyone on the board is not green. The fold is a button with
 * aria-expanded, remembered per person; on a narrow screen the panel sits
 * under the rest of the Today.
 */
export function CheckinsPanel({
  read,
  day,
  who,
}: {
  read: UseQueryResult<PodCheckinsResponse>;
  /** "today", or "on Mon 5 Oct" while a past day is shown. */
  day: string;
  /** Whose choice the fold remembers: the person acting. */
  who: string | null;
}) {
  const [open, setOpen] = useRememberedOpen("sm-checkins", who);
  const board = read.data;
  const summary = board ? checkinsSummary(board, day) : null;
  const attention = summary?.attention ?? 0;

  return (
    <aside
      aria-label={`Check-ins ${day}`}
      className={cn(
        "min-w-0 lg:sticky lg:top-36 lg:self-start",
        open ? "lg:w-[21rem]" : "lg:w-[16.5rem]",
      )}
    >
      <section className="rounded-3xl border border-grey-border bg-white p-4 lg:max-h-[calc(100vh-10rem-var(--op-float-room))] lg:overflow-y-auto">
        <h2 className="text-[17px] font-extrabold">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={LIST_ID}
            onClick={() => setOpen(!open)}
            className="-m-1 flex w-[calc(100%+0.5rem)] items-center gap-2 rounded-xl p-1 text-left hover:bg-grey-fill"
          >
            <span className="min-w-0 flex-1">Check-ins {day}</span>
            {attention > 0 ? (
              <span
                title={followUpWords(attention)}
                className="inline-flex h-6 min-w-6 flex-none items-center justify-center rounded-full bg-rag-red px-1.5 text-[14px] font-extrabold leading-none text-white animate-op-pulse"
              >
                <span aria-hidden>!</span>
                <span className="sr-only">{followUpWords(attention)}</span>
              </span>
            ) : null}
            <ChevronDown
              size={18}
              aria-hidden
              className={cn("flex-none text-grey-secondary", open && "rotate-180")}
            />
          </button>
        </h2>
        <p className="mt-1 text-[13px] text-grey-body">
          {summary
            ? summary.text
            : read.error
              ? "The check-ins could not be read."
              : read.isLoading
                ? "Loading the check-ins…"
                : null}
          {attention > 0 ? (
            <span className="font-bold text-rag-red"> · {followUpWords(attention)}</span>
          ) : null}
        </p>
        <div id={LIST_ID} hidden={!open} className="mt-2">
          {open ? (
            <PanelState
              isLoading={read.isLoading}
              error={read.error}
              onRetry={() => void read.refetch()}
              isEmpty={(board?.developers ?? []).length === 0}
              emptyText="Nobody in this pod is asked to check in."
            >
              <ul>
                {(board?.developers ?? []).map((dev) => {
                  const word = boardWord(dev, board?.as_of ?? "");
                  return (
                    <Row
                      key={dev.developer_id}
                      rag={boardRag(dev)}
                      title={dev.developer_name}
                      meta={boardMeta(dev, board?.as_of ?? "", { day: formatDay, today: day })}
                      right={
                        <RagChip tone={word.tone} className="h-6 px-2.5 text-[12px]">
                          {word.word}
                        </RagChip>
                      }
                    />
                  );
                })}
              </ul>
            </PanelState>
          ) : null}
        </div>
      </section>
    </aside>
  );
}
