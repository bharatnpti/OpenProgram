import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError, apiClient } from "../../api/client";
import type { CheckinPreferenceResponse } from "../../api/schema";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { AdminSelect } from "../admin/AdminSelect";
import { errorMessage, weekdayOptions } from "../admin/adminTypes";
import { FormField } from "../admin/FormField";
import {
  daysLabel,
  deviceTimezone,
  draftFrom,
  isNoCheckin,
  myCheckinPreferenceKey,
  scheduleChanges,
  sortedDays,
  timezoneLabel,
  timezoneOptions,
  useMyCheckinPreference,
  type ScheduleDraft,
} from "./myCheckinPreference";

// The select's value for a stored null zone, which means the team default.
const TEAM_ZONE = "";

/**
 * The avatar-menu entry for the person's own check-in schedule, with the
 * current days and zone in plain words. It isn't shown to someone with no
 * member record, because the bot never asks them.
 */
export function CheckinScheduleMenuItem({ onSelect }: { onSelect: () => void }) {
  const preference = useMyCheckinPreference();
  if (isNoCheckin(preference.error)) return null;

  const summary = preference.data
    ? `${daysLabel(preference.data.weekdays)} · ${timezoneLabel(preference.data.timezone)}`
    : preference.isError
      ? "Couldn't load"
      : "Loading…";

  return (
    <button
      type="button"
      onClick={onSelect}
      className="flex w-full flex-col items-start rounded-lg px-3.5 py-2.5 text-left text-sm hover:bg-grey-fill"
    >
      Check-in schedule
      <span className="text-[12px] text-grey-secondary">{summary}</span>
    </button>
  );
}

export function CheckinScheduleDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const preference = useMyCheckinPreference();

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Your check-in schedule">
      {preference.data ? (
        <ScheduleForm preference={preference.data} onClose={() => onOpenChange(false)} />
      ) : preference.isError ? (
        <div className="flex flex-col gap-4">
          <p className="text-[14px] text-grey-body">
            {isNoCheckin(preference.error)
              ? "You have no member record, so the bot doesn't ask you to check in."
              : `Your check-in schedule could not be loaded: ${errorMessage(preference.error)}`}
          </p>
          <div className="flex justify-end">
            <Pill variant="ghost" size="md" onClick={() => onOpenChange(false)}>
              Close
            </Pill>
          </div>
        </div>
      ) : (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      )}
    </Modal>
  );
}

/**
 * Mounted each time the dialog opens, so it starts from the stored values and
 * compares against that same snapshot. A refetch while it is open can't make an
 * untouched field look edited and get sent back.
 */
function ScheduleForm({
  preference,
  onClose,
}: {
  preference: CheckinPreferenceResponse;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [initial] = useState<ScheduleDraft>(() => draftFrom(preference));
  const [draft, setDraft] = useState<ScheduleDraft>(initial);
  const device = deviceTimezone();
  const [zones] = useState(() => timezoneOptions(initial.timezone, device));

  const changes = scheduleChanges(initial, draft);
  const changed = Object.keys(changes).length > 0;
  const noDays = draft.weekdays.length === 0;

  const save = useMutation({
    mutationFn: () => apiClient.updateCheckinPreference(changes),
    onSuccess: (saved) => {
      queryClient.setQueryData(myCheckinPreferenceKey, saved);
      toast.success("Check-in schedule saved. It applies from your next check-in.");
      onClose();
    },
    onError: (error) =>
      toast.error(
        error instanceof ApiError && error.status === 422
          ? "The server didn't accept that time zone. Pick another."
          : errorMessage(error),
      ),
  });

  function toggleDay(day: number) {
    setDraft((current) => ({
      ...current,
      weekdays: current.weekdays.includes(day)
        ? current.weekdays.filter((item) => item !== day)
        : sortedDays([...current.weekdays, day]),
    }));
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (changed && !noDays) save.mutate();
      }}
    >
      <p className="text-[13px] text-grey-secondary">
        The bot sends your team's check-in at one time for everyone, so the time isn't set here. You
        choose the days it asks you and your time zone.
      </p>

      <FormField
        label="Days the bot asks you"
        error={noDays ? "Pick at least one day." : undefined}
      >
        <div role="group" aria-label="Days the bot asks you" className="flex flex-wrap gap-2">
          {weekdayOptions.map((day) => {
            const selected = draft.weekdays.includes(day.value);
            return (
              <button
                key={day.value}
                type="button"
                aria-pressed={selected}
                onClick={() => toggleDay(day.value)}
                className={cn(
                  "flex h-9 items-center rounded-full border px-3.5 text-[13px] font-bold",
                  selected
                    ? "border-magenta bg-magenta text-white"
                    : "border-grey-border text-grey-secondary hover:bg-grey-fill",
                )}
              >
                {day.label}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-[12px] text-grey-secondary">
          On days you leave off, the bot doesn't ask you.
        </p>
      </FormField>

      <FormField label="Your time zone" htmlFor="checkin-schedule-timezone">
        <AdminSelect
          id="checkin-schedule-timezone"
          value={draft.timezone ?? TEAM_ZONE}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              timezone: event.target.value === TEAM_ZONE ? null : event.target.value,
            }))
          }
        >
          {initial.timezone === null ? <option value={TEAM_ZONE}>Team time zone</option> : null}
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </AdminSelect>
        <p className="mt-1.5 text-[12px] text-grey-secondary">
          Decides which day your reply counts for.
          {device && device !== draft.timezone ? (
            <>
              {" "}
              <button
                type="button"
                onClick={() => setDraft((current) => ({ ...current, timezone: device }))}
                className="font-bold text-magenta"
              >
                Use this device's time zone ({device})
              </button>
            </>
          ) : null}
        </p>
      </FormField>

      {changed ? (
        <div className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px]">
          <div className="font-bold">What changes</div>
          {changes.weekdays ? (
            <div className="mt-1">
              Days: {daysLabel(initial.weekdays)} → {daysLabel(draft.weekdays)}
            </div>
          ) : null}
          {"timezone" in changes ? (
            <div className="mt-1">
              Time zone: {timezoneLabel(initial.timezone)} → {timezoneLabel(draft.timezone)}
            </div>
          ) : null}
          <div className="mt-1 text-grey-secondary">
            It applies from your next check-in. One the bot has already sent isn't changed.
          </div>
        </div>
      ) : null}

      <div className="flex justify-end gap-3">
        <Pill variant="ghost" size="md" type="button" onClick={onClose}>
          Cancel
        </Pill>
        <Pill
          variant="primary"
          size="md"
          type="submit"
          disabled={!changed || noDays || save.isPending}
        >
          {save.isPending ? "Saving…" : "Save"}
        </Pill>
      </div>
    </form>
  );
}
