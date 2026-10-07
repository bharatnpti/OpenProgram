import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  CheckinPreferenceResponse,
  ConfigNodeResponse,
  WriteBackConsent,
} from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { weekdaysLabel } from "../../lib/format";
import { cn } from "../../lib/utils";
import { currentZoneName } from "../../lib/zones";
import { clockTime } from "../checkin/schedule";
import { AdminDialog, Problems, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText, minutesLabel } from "./adminWords";
import {
  type PrefDraft,
  type PrefField,
  PREF_FIELDS,
  changesFrom,
  defaultValue,
  draftFrom,
  draftProblems,
  editField,
  inheritedSummary,
  sameValue,
  toDefault,
  toEveryDefault,
} from "./checkinForm";
import { useMembers } from "./members";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const PREFS_KEY = ["config", "checkin-preferences"] as const;

const CONSENT_WORDS: Record<WriteBackConsent, string> = {
  always_ask: "asks each time",
  auto_apply: "applies without asking",
  never: "never",
};

const CONSENT_OPTIONS: { value: WriteBackConsent; label: string }[] = [
  { value: "always_ask", label: "Ask each time before changing their Jira issues (the default)" },
  { value: "auto_apply", label: "Apply their check-in to their Jira issues without asking" },
  { value: "never", label: "Never change their Jira issues" },
];

/** A value as the table shows it: "Mon–Fri", "Europe/Berlin", "4 h". */
function valueLabel(field: PrefField, value: unknown): string {
  if (field === "weekdays") return weekdaysLabel(value as number[]);
  if (field === "timezone") return String(value ?? "—");
  return minutesLabel(value as number);
}

function storedValue(pref: CheckinPreferenceResponse, field: PrefField): unknown {
  return field === "timezone" ? (pref.timezone ?? pref.defaults.timezone) : pref[field];
}

/** Every time zone this browser knows, for the time zone field's suggestions. */
function timeZones(): string[] {
  const all =
    (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.(
      "timeZone",
    ) ?? [];
  // Under their current names: the browser lists a few by an old one the server rejects.
  const current = Array.from(new Set(all.map(currentZoneName)));
  return current.includes("UTC") ? current : ["UTC", ...current];
}

/**
 * Who is asked to check in, on which days and in which time zone, how long the
 * bot waits before a nudge and before giving up, and each member's write-back
 * consent. A value follows the team default until it is set for the member,
 * and can be put back to the default from the Change dialog.
 */
export function CheckinsTab() {
  const members = useMembers();
  const prefs = useQuery({
    queryKey: PREFS_KEY,
    queryFn: () => apiClient.configCheckinPreferences(),
  });
  const tenant = useQuery({
    queryKey: ["config", "tenant-writeback"],
    queryFn: () => apiClient.configTenantWriteback(),
  });
  const list = members.data ?? [];
  // The API reads consent one member at a time (no list endpoint), so these
  // are cached for a while instead of refetched on every visit.
  const consents = useQueries({
    queries: list.map((m) => ({
      queryKey: ["config", "consent", m.id],
      queryFn: () => apiClient.configMemberWritebackConsent(m.id),
      staleTime: 5 * 60_000,
    })),
  });
  const consentOf = (id: string) =>
    consents.find((c) => c.data?.developer_id === id)?.data?.consent;
  const prefOf = (id: string) => prefs.data?.find((p) => p.developer_id === id);
  const defaults = prefs.data?.[0]?.defaults;

  return (
    <PanelState
      needs="an admin"
      isLoading={members.isLoading || prefs.isLoading}
      error={members.error ?? prefs.error}
      onRetry={() => {
        void members.refetch();
        void prefs.refetch();
      }}
      isEmpty={list.length === 0}
      emptyText="No members yet. Import people from the chat directory first."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
        {defaults ? (
          <p className="text-[13px] text-grey-body">
            Team default: {weekdaysLabel(defaults.weekdays)} · {defaults.timezone} · nudge after{" "}
            {minutesLabel(defaults.reply_wait_seconds)} · give up after{" "}
            {minutesLabel(defaults.final_reply_wait_seconds)}. Check-ins go out at{" "}
            {clockTime(defaults.local_time)}, one time for everyone, read on each person's own
            clock.
          </p>
        ) : null}
        <p className="text-[12px] text-grey-secondary">
          <SourceTag set={false} /> follows the team default and changes with it. <SourceTag set />{" "}
          is chosen for that member; Change puts it back to the default.
        </p>
        <TableBox>
          <table className="w-full min-w-[860px] border-collapse">
            <thead>
              <tr>
                <th className={th}>Member</th>
                <th className={th}>Days</th>
                <th className={th}>Time zone</th>
                <th className={th}>Nudge after</th>
                <th className={th}>Give up after</th>
                <th className={th}>Write-back</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {list.map((m) => {
                const p = prefOf(m.id);
                const consent = consentOf(m.id);
                const cell = (field: PrefField) =>
                  p ? (
                    <>
                      {valueLabel(field, storedValue(p, field))}
                      <SourceTag
                        set={!p.inherited.includes(field)}
                        sameAsDefault={sameValue(
                          field,
                          storedValue(p, field),
                          defaultValue(p, field),
                        )}
                      />
                    </>
                  ) : (
                    "—"
                  );
                return (
                  <tr key={m.id}>
                    <td className={td}>
                      <span className="font-bold">{m.name}</span>
                      <span className="block text-[11px] text-grey-secondary">
                        {p ? inheritedSummary(p.inherited) : m.id}
                      </span>
                    </td>
                    <td className={td}>{cell("weekdays")}</td>
                    <td className={td}>{cell("timezone")}</td>
                    <td className={td}>{cell("reply_wait_seconds")}</td>
                    <td className={td}>{cell("final_reply_wait_seconds")}</td>
                    <td className={td}>
                      {consent ? (
                        <RagChip
                          tone={
                            consent === "auto_apply"
                              ? "success"
                              : consent === "never"
                                ? "neutral"
                                : "info"
                          }
                          className="h-6 px-2.5 text-[12px]"
                        >
                          {CONSENT_WORDS[consent]}
                        </RagChip>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={`${td} text-right`}>
                      {p ? <EditMember member={m} pref={p} consent={consent} /> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableBox>
        <p className="text-[13px] text-grey-body">
          Write-back is <b>{tenant.isLoading ? "…" : tenant.data?.enabled ? "on" : "off"}</b> for
          this tenant
          {tenant.data?.source === "default" ? " (the default)" : ""}.{" "}
          {tenant.data?.enabled
            ? "A member's consent decides whether their check-in moves their Jira issues."
            : "Consent is recorded, but nothing is written to Jira until the switch is on."}
        </p>
      </div>
    </PanelState>
  );
}

/** "default" in grey, or "set" in magenta; a set value equal to the default says so on hover. */
function SourceTag({ set, sameAsDefault }: { set: boolean; sameAsDefault?: boolean }) {
  return set ? (
    <span
      className="ml-1 text-[11px] font-bold text-magenta"
      title={
        sameAsDefault
          ? "Set for this member to the same value as the team default; it won't follow a change to the default."
          : "Set for this member."
      }
    >
      set
    </span>
  ) : (
    <span className="ml-1 text-[11px] text-grey-secondary" title="Follows the team default.">
      default
    </span>
  );
}

/** Only the fields someone touches are sent, so a member keeps following any default left alone. */
function EditMember({
  member,
  pref,
  consent,
}: {
  member: ConfigNodeResponse;
  pref: CheckinPreferenceResponse;
  consent: WriteBackConsent | undefined;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PrefDraft>(() => draftFrom(pref));
  const [nextConsent, setNextConsent] = useState<WriteBackConsent | "">(consent ?? "");
  const [attempted, setAttempted] = useState(false);
  const zones = useMemo(() => (open ? timeZones() : []), [open]);
  const first = member.name.split(" ")[0] || member.name;

  const changes = changesFrom(pref, draft);
  const consentChanged = nextConsent !== "" && nextConsent !== consent;
  const changed = Object.keys(changes).length > 0 || consentChanged;
  const problems = draftProblems(draft);
  const anySet = PREF_FIELDS.some((field) => !draft.inherited.includes(field));

  const save = useMutation({
    mutationFn: async () => {
      if (Object.keys(changes).length > 0) {
        await apiClient.updateConfigMemberCheckinPreference(pref.developer_id, changes);
      }
      if (consentChanged && nextConsent) {
        await apiClient.updateConfigMemberWritebackConsent(pref.developer_id, {
          consent: nextConsent,
        });
      }
    },
    onSuccess: () => {
      toast.success(`Saved ${member.name}. Changes apply from their next check-in.`);
      void queryClient.invalidateQueries({ queryKey: PREFS_KEY });
      void queryClient.invalidateQueries({ queryKey: ["config", "consent", pref.developer_id] });
      setOpen(false);
    },
    // A failed second write leaves the first saved; re-reading shows what is stored.
    onError: (error) => {
      toast.error(errorText(error));
      void queryClient.invalidateQueries({ queryKey: PREFS_KEY });
    },
  });

  const source = (field: PrefField) => {
    const fallback = valueLabel(field, defaultValue(pref, field));
    return draft.inherited.includes(field) ? (
      <p className={hintClass}>Team default ({fallback}). It changes when the default does.</p>
    ) : (
      <p className={hintClass}>
        Set for {first}. The team default is {fallback}.{" "}
        <button
          type="button"
          className="font-bold text-magenta"
          onClick={() => setDraft((current) => toDefault(current, pref, field))}
        >
          Use team default
        </button>
      </p>
    );
  };

  return (
    <>
      <Pill
        size="sm"
        variant="ghost"
        onClick={() => {
          setDraft(draftFrom(pref));
          setNextConsent(consent ?? "");
          setAttempted(false);
          setOpen(true);
        }}
      >
        Change
      </Pill>
      <AdminDialog
        open={open}
        onOpenChange={setOpen}
        title={`Check-ins for ${member.name}`}
        description="Applies from their next check-in, never to one already sent."
      >
        <form
          className="mt-4 grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            setAttempted(true);
            if (problems.length === 0 && changed) save.mutate();
          }}
        >
          {anySet ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-grey-fill px-4 py-3 text-[13px]">
              <span>{inheritedSummary(draft.inherited)}.</span>
              <Pill
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setDraft((current) => toEveryDefault(current, pref))}
              >
                Use every team default
              </Pill>
            </div>
          ) : null}
          <fieldset>
            <legend className={labelClass}>Asked on</legend>
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((day, index) => {
                const on = draft.weekdays.includes(index);
                return (
                  <button
                    key={day}
                    type="button"
                    aria-pressed={on}
                    className={cn(
                      "h-9 rounded-full border px-3 text-[13px] font-bold",
                      on ? "border-ink bg-ink text-white" : "border-grey-border text-grey-body",
                    )}
                    onClick={() =>
                      setDraft((current) =>
                        editField(
                          current,
                          pref,
                          "weekdays",
                          on
                            ? current.weekdays.filter((d) => d !== index)
                            : [...current.weekdays, index],
                        ),
                      )
                    }
                  >
                    {day}
                  </button>
                );
              })}
            </div>
            {source("weekdays")}
          </fieldset>
          <div>
            <label htmlFor={`cm-tz-${member.id}`} className={labelClass}>
              Time zone
            </label>
            <input
              id={`cm-tz-${member.id}`}
              className={inputClass}
              list={`cm-tz-list-${member.id}`}
              value={draft.timezone}
              autoComplete="off"
              onChange={(e) =>
                setDraft((current) => editField(current, pref, "timezone", e.target.value))
              }
            />
            <datalist id={`cm-tz-list-${member.id}`}>
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
            {source("timezone")}
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={`cm-wait-${member.id}`} className={labelClass}>
                Nudge after (minutes)
              </label>
              <input
                id={`cm-wait-${member.id}`}
                inputMode="decimal"
                className={inputClass}
                value={draft.nudgeMinutes}
                onChange={(e) =>
                  setDraft((current) =>
                    editField(current, pref, "reply_wait_seconds", e.target.value),
                  )
                }
              />
              {source("reply_wait_seconds")}
            </div>
            <div>
              <label htmlFor={`cm-final-${member.id}`} className={labelClass}>
                Give up after (minutes)
              </label>
              <input
                id={`cm-final-${member.id}`}
                inputMode="decimal"
                className={inputClass}
                value={draft.giveUpMinutes}
                onChange={(e) =>
                  setDraft((current) =>
                    editField(current, pref, "final_reply_wait_seconds", e.target.value),
                  )
                }
              />
              {source("final_reply_wait_seconds")}
            </div>
          </div>
          <div>
            <label htmlFor={`cm-consent-${member.id}`} className={labelClass}>
              Write-back consent
            </label>
            <select
              id={`cm-consent-${member.id}`}
              className={inputClass}
              value={nextConsent}
              onChange={(e) => setNextConsent(e.target.value as WriteBackConsent)}
            >
              {nextConsent === "" ? (
                <option value="" disabled>
                  Reading their consent…
                </option>
              ) : null}
              {CONSENT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          {attempted ? <Problems problems={problems} /> : null}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {!changed ? (
              <span className="text-[12px] text-grey-secondary">No changes yet.</span>
            ) : null}
            <Pill type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Pill>
            <Pill type="submit" size="sm" disabled={!changed || save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Pill>
          </div>
        </form>
      </AdminDialog>
    </>
  );
}
