import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { cn } from "../../lib/utils";
import type { CheckinPreferenceResponse, ConfigNodeResponse } from "../../api/schema";
import { NodeSelect } from "./AdminSelect";
import { FormField } from "./FormField";
import { RangeSlider } from "./RangeSlider";
import { errorMessage, weekdayOptions } from "./adminTypes";

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
  const [replyWait, setReplyWait] = useState(300);
  const [finalReplyWait, setFinalReplyWait] = useState(900);

  const updateMutation = useMutation({
    mutationFn: () =>
      apiClient.updateConfigMemberCheckinPreference(memberId, {
        local_time: localTime,
        timezone,
        weekdays,
        reply_wait_seconds: replyWait,
        final_reply_wait_seconds: finalReplyWait,
      }),
    onSuccess: async () => {
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
          <FormField label="Weekdays">
            <div className="flex flex-wrap gap-2">
              {weekdayOptions.map((day) => (
                <button
                  key={day.value}
                  type="button"
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
          <RangeSlider
            label="Reply wait (seconds)"
            min={60}
            max={3600}
            step={60}
            value={replyWait}
            valueLabel={`${replyWait}s`}
            onChange={(event) => setReplyWait(Number(event.target.value))}
          />
          <RangeSlider
            label="Final reply wait (seconds)"
            min={60}
            max={7200}
            step={60}
            value={finalReplyWait}
            valueLabel={`${finalReplyWait}s`}
            onChange={(event) => setFinalReplyWait(Number(event.target.value))}
          />
          <div>
            <Pill
              variant="primary"
              size="md"
              disabled={!memberId || updateMutation.isPending}
              onClick={() => updateMutation.mutate()}
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
                  <div className="text-[14px] font-bold">{pref.developer_id}</div>
                  <div className="mt-0.5 text-[13px] text-grey-secondary">
                    {pref.local_time} / {pref.timezone ?? "UTC"} / {pref.weekdays.join(",")}
                  </div>
                  <div className="mt-1.5">
                    <RagChip tone="info">{pref.reply_wait_seconds}s wait</RagChip>
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
