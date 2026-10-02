import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDuration } from "../../lib/duration";
import { cn } from "../../lib/utils";
import type {
  CheckinPreferenceResponse,
  CheckinPreferenceUpdateRequest,
  ConfigNodeResponse,
} from "../../api/schema";
import { NodeSelect } from "./AdminSelect";
import { DurationField } from "./DurationField";
import { FormField } from "./FormField";
import { errorMessage, weekdayOptions } from "./adminTypes";

/** One click each for the live-nudge demo (1 min) and the 4 h / 8 h defaults. */
const waitPresets = [60, 900, 3600, 14400, 28800];

export function CheckinPreferencesPanel({
  members,
  preferences,
  onChanged,
}: {
  members: ConfigNodeResponse[];
  preferences: CheckinPreferenceResponse[];
  onChanged: () => Promise<void>;
}) {
  const [memberId, setMemberId] = useState("");
  const [localTime, setLocalTime] = useState("09:00");
  const [timezone, setTimezone] = useState("UTC");
  const [weekdays, setWeekdays] = useState<number[]>([0, 1, 2, 3, 4]);
  const [replyWait, setReplyWait] = useState<number | null>(300);
  const [finalReplyWait, setFinalReplyWait] = useState<number | null>(900);
  // What the selected member has stored, so a save sends only what changed.
  const [loaded, setLoaded] = useState<CheckinPreferenceResponse | null>(null);
  // With no days the bot never asks the member again, and the API refuses it.
  const noDays = weekdays.length === 0;

  const updateMutation = useMutation({
    mutationFn: (changes: CheckinPreferenceUpdateRequest) =>
      apiClient.updateConfigMemberCheckinPreference(memberId, changes),
    onSuccess: async (saved) => {
      // The next save compares against what is stored now, unless the admin
      // has moved on to another member meanwhile.
      setLoaded((current) => (current?.developer_id === saved.developer_id ? saved : current));
      await onChanged();
      toast.success("Check-in preference saved.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  function toggleWeekday(day: number) {
    setWeekdays((current) =>
      current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort(),
    );
  }

  function selectMember(nextMember: string) {
    setMemberId(nextMember);
    const pref = preferences.find((item) => item.developer_id === nextMember);
    setLoaded(pref ?? null);
    if (pref) {
      setLocalTime(pref.local_time.slice(0, 5));
      setTimezone(pref.timezone ?? "UTC");
      setWeekdays(pref.weekdays);
      setReplyWait(pref.reply_wait_seconds);
      setFinalReplyWait(pref.final_reply_wait_seconds);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[18px] font-bold">Check-in preferences</h2>
        <p className="mt-1 text-[13px] text-grey-secondary">
          Set local check-in timing, weekdays, and reply windows per member.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex flex-col gap-4 rounded-3xl border border-grey-border bg-white p-5">
          <FormField label="Member" htmlFor="checkin-member">
            <NodeSelect
              id="checkin-member"
              value={memberId}
              onChange={selectMember}
              items={members}
              placeholder="Select member"
            />
          </FormField>
          <div className="grid gap-3 md:grid-cols-2">
            <FormField label="Local time" htmlFor="checkin-time">
              <TextInput
                id="checkin-time"
                type="time"
                value={localTime}
                onChange={(event) => setLocalTime(event.target.value)}
              />
            </FormField>
            <FormField label="Timezone" htmlFor="checkin-timezone">
              <TextInput
                id="checkin-timezone"
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              />
            </FormField>
          </div>
          <FormField label="Weekdays" error={noDays ? "Pick at least one day." : undefined}>
            <div role="group" aria-label="Weekdays" className="flex flex-wrap gap-2">
              {weekdayOptions.map((day) => (
                <button
                  key={day.value}
                  type="button"
                  aria-pressed={weekdays.includes(day.value)}
                  onClick={() => toggleWeekday(day.value)}
                  className={cn(
                    "flex h-9 items-center rounded-full border px-3.5 text-[13px] font-bold",
                    weekdays.includes(day.value)
                      ? "border-magenta bg-magenta text-white"
                      : "border-grey-border text-grey-secondary hover:bg-grey-fill",
                  )}
                >
                  {day.label}
                </button>
              ))}
            </div>
          </FormField>
          <DurationField
            id="checkin-reply-wait"
            label="Reply wait"
            hint="How long to wait for a reply before the first nudge."
            value={replyWait}
            presets={waitPresets}
            onChange={setReplyWait}
          />
          <DurationField
            id="checkin-final-reply-wait"
            label="Final reply wait"
            hint="How long to wait after the last escalation before the check-in closes as unanswered."
            value={finalReplyWait}
            presets={waitPresets}
            onChange={setFinalReplyWait}
          />
          <div>
            <Pill
              variant="primary"
              size="md"
              disabled={
                !memberId ||
                noDays ||
                replyWait === null ||
                finalReplyWait === null ||
                updateMutation.isPending
              }
              onClick={() => {
                if (noDays || replyWait === null || finalReplyWait === null) {
                  return;
                }
                updateMutation.mutate(
                  preferenceChanges(loaded, {
                    local_time: localTime,
                    timezone,
                    weekdays,
                    reply_wait_seconds: replyWait,
                    final_reply_wait_seconds: finalReplyWait,
                  }),
                );
              }}
            >
              {updateMutation.isPending ? "Saving…" : "Save preference"}
            </Pill>
          </div>
        </div>

        <div>
          <div className="mb-2 text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
            Configured
          </div>
          {preferences.length === 0 ? (
            <div className="rounded-3xl border border-grey-border bg-white px-5 py-10 text-center text-[14px] text-grey-secondary">
              No preferences yet.
            </div>
          ) : (
            <div className="max-h-[32rem] overflow-y-auto rounded-3xl border border-grey-border bg-white">
              {preferences.map((pref, index) => (
                <div
                  key={pref.developer_id}
                  className={cn("px-4 py-3", index === 0 ? "" : "border-t border-grey-fill")}
                >
                  {/* The member picker beside this list already shows names and
                      the weekday chips already have labels, so an admin was
                      reading "U1001 / 0,1,2,3,4" next to "Asha Rao" and
                      "Mon Tue Wed Thu Fri". */}
                  <div className="text-[14px] font-bold">
                    {memberName(pref.developer_id, members)}
                  </div>
                  <div className="mt-0.5 text-[13px] text-grey-secondary">
                    {pref.local_time} / {pref.timezone ?? "UTC"} / {weekdayLabels(pref.weekdays)}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <RagChip tone="info">{formatDuration(pref.reply_wait_seconds)} wait</RagChip>
                    <RagChip tone="neutral">
                      {formatDuration(pref.final_reply_wait_seconds)} final wait
                    </RagChip>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

type PreferenceForm = {
  local_time: string;
  timezone: string;
  weekdays: number[];
  reply_wait_seconds: number;
  final_reply_wait_seconds: number;
};

/**
 * The fields that differ from what is stored. The PUT keeps anything left out,
 * so a save never rewrites a value the form only displays: a reply window, a
 * time stored with seconds, or a timezone left to the workspace default.
 */
function preferenceChanges(
  loaded: CheckinPreferenceResponse | null,
  form: PreferenceForm,
): CheckinPreferenceUpdateRequest {
  if (!loaded) {
    return form;
  }
  const changes: CheckinPreferenceUpdateRequest = {};
  if (form.local_time !== loaded.local_time.slice(0, 5)) {
    changes.local_time = form.local_time;
  }
  if (form.timezone !== (loaded.timezone ?? "UTC")) {
    changes.timezone = form.timezone;
  }
  if ([...form.weekdays].sort().join() !== [...loaded.weekdays].sort().join()) {
    changes.weekdays = form.weekdays;
  }
  if (form.reply_wait_seconds !== loaded.reply_wait_seconds) {
    changes.reply_wait_seconds = form.reply_wait_seconds;
  }
  if (form.final_reply_wait_seconds !== loaded.final_reply_wait_seconds) {
    changes.final_reply_wait_seconds = form.final_reply_wait_seconds;
  }
  return changes;
}

/** A member's name, falling back to the id before the directory has loaded. */
function memberName(developerId: string, members: ConfigNodeResponse[]): string {
  return members.find((member) => member.id === developerId)?.name ?? developerId;
}

/** Weekdays as the short labels the chips above already use. */
function weekdayLabels(weekdays: number[]): string {
  if (weekdays.length === 0) {
    return "no days";
  }
  return weekdayOptions
    .filter((option) => weekdays.includes(option.value))
    .map((option) => option.label)
    .join(" ");
}
