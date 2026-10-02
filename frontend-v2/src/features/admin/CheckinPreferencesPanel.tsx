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
  CheckInPreferenceField,
  CheckinDefaultsResponse,
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
  const [form, setForm] = useState<PreferenceForm>(emptyForm);
  // What the selected member has stored, so a save sends only what changed.
  const [loaded, setLoaded] = useState<CheckinPreferenceResponse | null>(null);
  // With no days the bot never asks the member again, and the API refuses it.
  const noDays = form.weekdays.length === 0;
  const noZone = form.timezone.trim() === "";

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

  /**
   * An edit sets the field for this member, unless it puts back the team
   * default on a field that was following it: that stays inherited, so a
   * change and undo doesn't pin today's default.
   */
  function edit<F extends PrefField>(field: F, value: FieldValues[F]) {
    setForm((current) => {
      const next = { ...current, [field]: value };
      const followsDefault =
        loaded !== null &&
        loaded.inherited.includes(field) &&
        sameValue(field, value, defaultValue(loaded.defaults, field));
      return { ...next, inherited: withInherited(current.inherited, field, followsDefault) };
    });
  }

  /** Back to the team default: the save clears the member's own value. */
  function resetToDefault(field: PrefField) {
    if (!loaded) return;
    setForm((current) => ({
      ...current,
      [field]: defaultValue(loaded.defaults, field),
      inherited: withInherited(current.inherited, field, true),
    }));
  }

  function toggleWeekday(day: number) {
    edit(
      "weekdays",
      form.weekdays.includes(day)
        ? form.weekdays.filter((item) => item !== day)
        : [...form.weekdays, day].sort(),
    );
  }

  function selectMember(nextMember: string) {
    setMemberId(nextMember);
    const pref = preferences.find((item) => item.developer_id === nextMember);
    setLoaded(pref ?? null);
    if (pref) {
      setForm(formFrom(pref));
    }
  }

  /** Where a field's value comes from, with a way back to the team default. */
  function source(field: PrefField) {
    if (!loaded) return null;
    return (
      <InheritNote
        inherited={form.inherited.includes(field)}
        defaultLabel={valueLabel(field, defaultValue(loaded.defaults, field))}
        onUseDefault={() => resetToDefault(field)}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[18px] font-bold">Check-in preferences</h2>
        <p className="mt-1 text-[13px] text-grey-secondary">
          Set each member's time zone, check-in days, and reply windows. Check-ins go out at one
          time for the whole team. Anything not set for a member follows the team default, and
          changes when the default does.
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
          {/* No per-person time: check-ins go out on one tenant-wide
              schedule, so a time set here would change nothing. The API
              still stores one, and the form never sends it. */}
          <div>
            <FormField
              label="Timezone"
              htmlFor="checkin-timezone"
              error={noZone ? "Enter a time zone, or use the team default." : undefined}
            >
              <TextInput
                id="checkin-timezone"
                value={form.timezone}
                onChange={(event) => edit("timezone", event.target.value)}
              />
            </FormField>
            {source("timezone")}
          </div>
          <div>
            <FormField label="Weekdays" error={noDays ? "Pick at least one day." : undefined}>
              <div role="group" aria-label="Weekdays" className="flex flex-wrap gap-2">
                {weekdayOptions.map((day) => (
                  <button
                    key={day.value}
                    type="button"
                    aria-pressed={form.weekdays.includes(day.value)}
                    onClick={() => toggleWeekday(day.value)}
                    className={cn(
                      "flex h-9 items-center rounded-full border px-3.5 text-[13px] font-bold",
                      form.weekdays.includes(day.value)
                        ? "border-magenta bg-magenta text-white"
                        : "border-grey-border text-grey-secondary hover:bg-grey-fill",
                    )}
                  >
                    {day.label}
                  </button>
                ))}
              </div>
            </FormField>
            {source("weekdays")}
          </div>
          <DurationField
            id="checkin-reply-wait"
            label="Reply wait"
            hint="How long to wait for a reply before the first nudge."
            note={source("reply_wait_seconds")}
            value={form.reply_wait_seconds}
            presets={waitPresets}
            onChange={(seconds) => edit("reply_wait_seconds", seconds)}
          />
          <DurationField
            id="checkin-final-reply-wait"
            label="Final reply wait"
            hint="How long to wait after the last escalation before the check-in closes as unanswered."
            note={source("final_reply_wait_seconds")}
            value={form.final_reply_wait_seconds}
            presets={waitPresets}
            onChange={(seconds) => edit("final_reply_wait_seconds", seconds)}
          />
          <div>
            <Pill
              variant="primary"
              size="md"
              disabled={
                !memberId ||
                !loaded ||
                noDays ||
                noZone ||
                form.reply_wait_seconds === null ||
                form.final_reply_wait_seconds === null ||
                updateMutation.isPending
              }
              onClick={() => {
                if (!loaded || noDays || noZone) {
                  return;
                }
                updateMutation.mutate(preferenceChanges(loaded, form));
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
                    {pref.timezone ?? pref.defaults.timezone} / {weekdayLabels(pref.weekdays)}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <RagChip tone="info">{formatDuration(pref.reply_wait_seconds)} wait</RagChip>
                    <RagChip tone="neutral">
                      {formatDuration(pref.final_reply_wait_seconds)} final wait
                    </RagChip>
                  </div>
                  <div className="mt-1.5 text-[12px] text-grey-secondary">
                    {inheritedSummary(pref.inherited.filter(isFormField))}
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

/** "Team default · 4 h", or "Set for this member" with a way back to the default. */
function InheritNote({
  inherited,
  defaultLabel,
  onUseDefault,
}: {
  inherited: boolean;
  defaultLabel: string;
  onUseDefault: () => void;
}) {
  return (
    <p className="mt-1.5 text-[12px] text-grey-secondary">
      {inherited ? (
        <>Team default · {defaultLabel}</>
      ) : (
        <>
          Set for this member. The team default is {defaultLabel}.{" "}
          <button type="button" onClick={onUseDefault} className="font-bold text-magenta">
            Use team default
          </button>
        </>
      )}
    </p>
  );
}

/** The fields the console sets: every preference field but the unused per-person time. */
type PrefField = Exclude<CheckInPreferenceField, "local_time">;

/** What the form shows for each field; a wait is `null` while it can't be saved. */
type FieldValues = {
  timezone: string;
  weekdays: number[];
  reply_wait_seconds: number | null;
  final_reply_wait_seconds: number | null;
};

type PreferenceForm = FieldValues & {
  /** The fields that follow the team default. */
  inherited: PrefField[];
};

const formFields: PrefField[] = [
  "timezone",
  "weekdays",
  "reply_wait_seconds",
  "final_reply_wait_seconds",
];

/** Shown before a member is picked; Save stays off until one is. */
const emptyForm: PreferenceForm = {
  timezone: "UTC",
  weekdays: [0, 1, 2, 3, 4],
  reply_wait_seconds: 14400,
  final_reply_wait_seconds: 28800,
  inherited: formFields,
};

function formFrom(pref: CheckinPreferenceResponse): PreferenceForm {
  return {
    timezone: pref.timezone ?? pref.defaults.timezone,
    weekdays: pref.weekdays,
    reply_wait_seconds: pref.reply_wait_seconds,
    final_reply_wait_seconds: pref.final_reply_wait_seconds,
    inherited: pref.inherited.filter(isFormField),
  };
}

function isFormField(field: CheckInPreferenceField): field is PrefField {
  return (formFields as CheckInPreferenceField[]).includes(field);
}

function defaultValue<F extends PrefField>(
  defaults: CheckinDefaultsResponse,
  field: F,
): FieldValues[F] {
  const values: FieldValues = {
    timezone: defaults.timezone,
    weekdays: defaults.weekdays,
    reply_wait_seconds: defaults.reply_wait_seconds,
    final_reply_wait_seconds: defaults.final_reply_wait_seconds,
  };
  return values[field];
}

function sameValue(field: PrefField, left: unknown, right: unknown): boolean {
  if (field === "weekdays") {
    return [...(left as number[])].sort().join() === [...(right as number[])].sort().join();
  }
  return left === right;
}

function withInherited(inherited: PrefField[], field: PrefField, follows: boolean): PrefField[] {
  const others = inherited.filter((item) => item !== field);
  return follows ? formFields.filter((item) => item === field || others.includes(item)) : others;
}

/**
 * The fields to send. A field going back to the team default is sent as
 * `null`, a field set for the member is sent when it changed or was following
 * the default before, and anything else is left out, so the PUT keeps it: a
 * save never rewrites a value the form only displays, and never copies a team
 * default in.
 */
function preferenceChanges(
  loaded: CheckinPreferenceResponse,
  form: PreferenceForm,
): CheckinPreferenceUpdateRequest {
  const stored = formFrom(loaded);
  const changes: CheckinPreferenceUpdateRequest = {};
  for (const field of formFields) {
    const inheritedNow = form.inherited.includes(field);
    const inheritedBefore = stored.inherited.includes(field);
    if (inheritedNow) {
      if (!inheritedBefore) Object.assign(changes, { [field]: null });
    } else if (inheritedBefore || !sameValue(field, form[field], stored[field])) {
      Object.assign(changes, { [field]: form[field] });
    }
  }
  return changes;
}

/** A value as the form shows it: "UTC", "Mon Tue Wed", "4 h". */
function valueLabel(field: PrefField, value: unknown): string {
  if (field === "weekdays") return weekdayLabels(value as number[]);
  if (field === "reply_wait_seconds" || field === "final_reply_wait_seconds") {
    return formatDuration(value as number);
  }
  return String(value);
}

const fieldNames: Record<PrefField, string> = {
  timezone: "time zone",
  weekdays: "days",
  reply_wait_seconds: "reply wait",
  final_reply_wait_seconds: "final wait",
};

/** Which of a member's values are the team's: "Team default: days, final wait". */
function inheritedSummary(inherited: PrefField[]): string {
  if (inherited.length === 0) return "All set for this member";
  if (inherited.length === formFields.length) return "All team defaults";
  return `Team default: ${inherited.map((field) => fieldNames[field]).join(", ")}`;
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
