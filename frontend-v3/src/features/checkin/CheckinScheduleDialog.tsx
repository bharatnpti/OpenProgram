import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type RefObject } from "react";
import { toast } from "sonner";

import { ApiError, apiClient } from "../../api/client";
import type { CheckinPreferenceResponse } from "../../api/schema";
import { useReadOnly } from "../../app/viewingDate";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { isNoCheckin, useMyCheckinPreference } from "./useMyCheckinPreference";
import {
  MY_CHECKIN_PREFERENCE_KEY,
  askTimeWords,
  askedDays,
  changeLines,
  daysHintWords,
  daysLegendWords,
  daysWords,
  deviceTimezone,
  draftFrom,
  noCheckinWords,
  refusalWords,
  scheduleChanges,
  scheduleProblem,
  sendDays,
  timezoneOptions,
  toggleDay,
  waitWords,
  zoneEffectWords,
  type ScheduleDraft,
} from "./schedule";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// The select's value for following the team's zone.
const TEAM_ZONE = "";
const legend = "mb-2 text-[12px] font-bold uppercase tracking-wide text-grey-secondary";
const description = "mt-1 text-[13px] text-grey-body";

export function CheckinScheduleDialog({
  open,
  onOpenChange,
  returnFocusTo,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Where focus goes on close, since what opened it (a menu item) is gone by then. */
  returnFocusTo?: RefObject<HTMLElement | null>;
}) {
  const preference = useMyCheckinPreference(open);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop"
          onCloseAutoFocus={(event) => {
            if (!returnFocusTo?.current) return;
            event.preventDefault();
            returnFocusTo.current.focus();
          }}
        >
          <Dialog.Title className="text-[20px] font-extrabold">Your check-in schedule</Dialog.Title>
          {preference.data ? (
            // The form says when the bot asks, on the clock of the zone it shows.
            <ScheduleForm preference={preference.data} onClose={() => onOpenChange(false)} />
          ) : (
            <>
              <Dialog.Description className={description}>
                The bot asks your whole team at one time, so the time isn't yours to set. You choose
                the days it asks you and your time zone.
              </Dialog.Description>
              {preference.isError ? (
                <div className="mt-4 grid gap-4">
                  <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
                    {isNoCheckin(preference.error)
                      ? noCheckinWords(
                          (preference.error as ApiError).status,
                          (preference.error as ApiError).message,
                        )
                      : `Your schedule could not be loaded: ${(preference.error as Error).message}`}
                  </p>
                  <div className="flex justify-end">
                    <Dialog.Close asChild>
                      <Pill variant="ghost" size="sm">
                        Close
                      </Pill>
                    </Dialog.Close>
                  </div>
                </div>
              ) : (
                <p className="mt-4 text-[14px] text-grey-secondary">Loading…</p>
              )}
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * Mounted each time the dialog opens, so it starts from the stored values and
 * compares against that same snapshot: a refetch while it is open can't make
 * an untouched field look edited and get sent back.
 */
function ScheduleForm({
  preference,
  onClose,
}: {
  preference: CheckinPreferenceResponse;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { readOnly, reason } = useReadOnly();
  const [initial] = useState<ScheduleDraft>(() => draftFrom(preference));
  const [draft, setDraft] = useState<ScheduleDraft>(initial);
  const device = deviceTimezone();
  const [zones] = useState(() => timezoneOptions(initial.timezone, device));
  const team = preference.defaults;
  const send = preference.send;
  // Weekly: only the days the bot sends on, since any other would change
  // nothing. Otherwise every day: a day only skips a send that falls on it.
  const offered = sendDays(send);
  const teamDays = askedDays(team.weekdays, send);

  const changes = scheduleChanges(initial, draft);
  const changed = Object.keys(changes).length > 0;
  const problem = scheduleProblem(draft);
  const lines = changeLines(initial, draft, team.timezone, send);

  const save = useMutation({
    mutationFn: () => apiClient.updateCheckinPreference(changes),
    onSuccess: (saved) => {
      queryClient.setQueryData(MY_CHECKIN_PREFERENCE_KEY, saved);
      toast.success("Check-in schedule saved. It applies from your next check-in.");
      onClose();
    },
    onError: (error: Error) =>
      toast.error(
        error instanceof ApiError && error.status === 422
          ? `Not saved: ${refusalWords(error.detail) ?? "the server refused it."}`
          : error.message,
      ),
  });

  const chooseTeamDays = (follow: boolean) =>
    setDraft((current) => ({
      ...current,
      teamDays: follow,
      // Following the team shows the team's days; choosing starts from what is shown.
      weekdays: follow ? [...teamDays] : current.weekdays,
    }));

  return (
    <>
      <Dialog.Description className={description}>
        {askTimeWords(send, draft.timezone ?? team.timezone)}
      </Dialog.Description>
      <form
        className="mt-5 grid gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (changed && !problem && !readOnly) save.mutate();
        }}
      >
        <fieldset>
          <legend className={legend}>{daysLegendWords(send)}</legend>
          <div className="grid gap-2 text-[14px]">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="schedule-days"
                checked={draft.teamDays}
                onChange={() => chooseTeamDays(true)}
              />
              Your team's days: {daysWords(teamDays, send)}
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="schedule-days"
                checked={!draft.teamDays}
                onChange={() => chooseTeamDays(false)}
              />
              Days you choose
            </label>
          </div>
          <div role="group" aria-label="Days of the week" className="mt-3 flex flex-wrap gap-1.5">
            {offered.map((day) => {
              const label = DAYS[day];
              const on = draft.weekdays.includes(day);
              return (
                <button
                  key={label}
                  type="button"
                  aria-pressed={on}
                  disabled={draft.teamDays}
                  onClick={() =>
                    setDraft((current) => ({
                      ...current,
                      weekdays: toggleDay(current.weekdays, day),
                    }))
                  }
                  className={cn(
                    "h-9 rounded-full border px-3.5 text-[13px] font-bold disabled:cursor-not-allowed disabled:opacity-50",
                    on
                      ? "border-ink bg-ink text-white"
                      : "border-grey-border text-grey-body hover:bg-grey-fill",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-[12px] text-grey-secondary">
            {daysHintWords(send, draft.teamDays)}
          </p>
          {problem ? <p className="mt-1 text-[12px] font-bold text-rag-red">{problem}</p> : null}
        </fieldset>

        <div>
          <label htmlFor="schedule-zone" className={cn(legend, "block")}>
            Your time zone
          </label>
          <select
            id="schedule-zone"
            className="h-10 w-full rounded-xl border border-grey-border bg-white px-3 text-[14px]"
            value={draft.timezone ?? TEAM_ZONE}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                timezone: event.target.value === TEAM_ZONE ? null : event.target.value,
              }))
            }
          >
            {/* Always offered, so someone with a zone of their own can go back to the team's. */}
            <option value={TEAM_ZONE}>Your team's time zone ({team.timezone})</option>
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
          <p className="mt-2 text-[12px] text-grey-secondary">
            {zoneEffectWords(send)}
            {device && device !== draft.timezone ? (
              <>
                {" "}
                <button
                  type="button"
                  className="font-bold text-magenta"
                  onClick={() => setDraft((current) => ({ ...current, timezone: device }))}
                >
                  Use this device's ({device})
                </button>
              </>
            ) : null}
          </p>
        </div>

        <p className="text-[12px] text-grey-secondary">
          How long the bot waits for your reply ({waitWords(preference.reply_wait_seconds)}, then{" "}
          {waitWords(preference.final_reply_wait_seconds)}) is set by an admin: it decides when your
          scrum master and manager hear about a missed check-in.
        </p>

        {lines.length > 0 ? (
          <div className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px]">
            <p className="font-bold">What changes</p>
            {lines.map((line) => (
              <p key={line} className="mt-1">
                {line}
              </p>
            ))}
            <p className="mt-1 text-grey-secondary">
              It applies from your next check-in. One the bot has already sent isn't changed.
            </p>
          </div>
        ) : null}

        {readOnly ? <p className="text-[13px] font-bold text-rag-amber-deep">{reason}</p> : null}

        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill
            type="submit"
            size="sm"
            disabled={!changed || problem !== null || readOnly || save.isPending}
            title={reason ?? undefined}
          >
            {save.isPending ? "Saving…" : "Save"}
          </Pill>
        </div>
      </form>
    </>
  );
}
