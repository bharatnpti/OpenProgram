import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  CheckinPreferenceResponse,
  CheckinPreferenceUpdateRequest,
  WriteBackConsent,
} from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { weekdaysLabel } from "../../lib/format";
import { cn } from "../../lib/utils";
import { minutesLabel } from "./adminWords";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CONSENT_WORDS: Record<WriteBackConsent, string> = {
  always_ask: "always ask",
  auto_apply: "auto apply",
  never: "never",
};

/**
 * Who is asked to check in, on which days and in which time zone, how long the
 * bot waits before a nudge and before giving up, and each member's write-back
 * consent. Values a member follows from the team default are marked as such.
 */
export function CheckinsTab() {
  const members = useQuery({
    queryKey: ["config", "members"],
    queryFn: () => apiClient.configMembers(),
  });
  const prefs = useQuery({
    queryKey: ["config", "checkin-preferences"],
    queryFn: () => apiClient.configCheckinPreferences(),
  });
  const tenant = useQuery({
    queryKey: ["config", "tenant-writeback"],
    queryFn: () => apiClient.configTenantWriteback(),
  });
  const list = members.data ?? [];
  const consents = useQueries({
    queries: list.map((m) => ({
      queryKey: ["config", "consent", m.id],
      queryFn: () => apiClient.configMemberWritebackConsent(m.id),
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
      isEmpty={list.length === 0}
      emptyText="No members yet. Import people from the chat directory first."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
        {defaults ? (
          <p className="text-[13px] text-grey-body">
            Team default: {weekdaysLabel(defaults.weekdays)} · {defaults.timezone} · nudge after{" "}
            {minutesLabel(defaults.reply_wait_seconds)} · give up after{" "}
            {minutesLabel(defaults.final_reply_wait_seconds)}. Check-ins go out at one tenant-wide
            time.
          </p>
        ) : null}
        <TableBox>
          <table className="w-full min-w-[820px] border-collapse">
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
                const from = (field: string) =>
                  p?.inherited.includes(field as CheckinPreferenceResponse["inherited"][number]) ? (
                    <span className="ml-1 text-[11px] text-grey-secondary">default</span>
                  ) : (
                    <span className="ml-1 text-[11px] font-bold text-magenta">set</span>
                  );
                const consent = consentOf(m.id);
                return (
                  <tr key={m.id}>
                    <td className={td}>
                      <span className="font-bold">{m.name}</span>
                      <span className="block text-[11px] text-grey-secondary">{m.id}</span>
                    </td>
                    <td className={td}>
                      {p ? (
                        <>
                          {weekdaysLabel(p.weekdays)}
                          {from("weekdays")}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={td}>
                      {p ? (
                        <>
                          {p.timezone ?? defaults?.timezone ?? "—"}
                          {from("timezone")}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={td}>
                      {p ? (
                        <>
                          {minutesLabel(p.reply_wait_seconds)}
                          {from("reply_wait_seconds")}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={td}>
                      {p ? (
                        <>
                          {minutesLabel(p.final_reply_wait_seconds)}
                          {from("final_reply_wait_seconds")}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
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
                      {p ? <EditMember name={m.name} pref={p} consent={consent} /> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableBox>
        <p className="text-[13px] text-grey-body">
          Write-back is <b>{tenant.data?.enabled ? "on" : "off"}</b> for this tenant
          {tenant.data?.source === "default" ? " (the default)" : ""}.{" "}
          {tenant.data?.enabled
            ? "A member's consent decides whether their check-in moves their Jira issues."
            : "Consent is recorded, but nothing is written to Jira until the switch is on."}
        </p>
      </div>
    </PanelState>
  );
}

/** Only fields that change are sent, so a member keeps following any default left alone. */
function EditMember({
  name,
  pref,
  consent,
}: {
  name: string;
  pref: CheckinPreferenceResponse;
  consent: WriteBackConsent | undefined;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [weekdays, setWeekdays] = useState(pref.weekdays);
  const [timezone, setTimezone] = useState(pref.timezone ?? "");
  const [wait, setWait] = useState(String(Math.round(pref.reply_wait_seconds / 60)));
  const [finalWait, setFinalWait] = useState(
    String(Math.round(pref.final_reply_wait_seconds / 60)),
  );
  const [nextConsent, setNextConsent] = useState<WriteBackConsent | "">(consent ?? "");

  const save = useMutation({
    mutationFn: async () => {
      const body: CheckinPreferenceUpdateRequest = {};
      if (weekdays.join() !== pref.weekdays.join()) body.weekdays = weekdays;
      if (timezone.trim() && timezone.trim() !== (pref.timezone ?? ""))
        body.timezone = timezone.trim();
      if (Number(wait) * 60 !== pref.reply_wait_seconds)
        body.reply_wait_seconds = Number(wait) * 60;
      if (Number(finalWait) * 60 !== pref.final_reply_wait_seconds)
        body.final_reply_wait_seconds = Number(finalWait) * 60;
      if (Object.keys(body).length > 0) {
        await apiClient.updateConfigMemberCheckinPreference(pref.developer_id, body);
      }
      if (nextConsent && nextConsent !== consent) {
        await apiClient.updateConfigMemberWritebackConsent(pref.developer_id, {
          consent: nextConsent,
        });
      }
    },
    onSuccess: () => {
      toast.success(`Saved ${name}. Changes apply from their next check-in.`);
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      setOpen(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const input = "h-10 w-full rounded-xl border border-grey-border px-3 text-[14px]";
  const label = "mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setWeekdays(pref.weekdays);
          setTimezone(pref.timezone ?? "");
          setWait(String(Math.round(pref.reply_wait_seconds / 60)));
          setFinalWait(String(Math.round(pref.final_reply_wait_seconds / 60)));
          setNextConsent(consent ?? "");
        }
      }}
    >
      <Dialog.Trigger asChild>
        <Pill size="sm" variant="ghost">
          Change
        </Pill>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop">
          <Dialog.Title className="text-[20px] font-extrabold">Check-ins for {name}</Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] text-grey-body">
            Applies from their next check-in, never to one already sent.
          </Dialog.Description>
          <form
            className="mt-4 grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (weekdays.length === 0) {
                toast.error(
                  "Pick at least one day: no days would stop their check-ins without saying so.",
                );
                return;
              }
              save.mutate();
            }}
          >
            <fieldset>
              <legend className={label}>Asked on</legend>
              <div className="flex flex-wrap gap-1.5">
                {WEEKDAYS.map((day, i) => {
                  const on = weekdays.includes(i);
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
                        setWeekdays(on ? weekdays.filter((d) => d !== i) : [...weekdays, i].sort())
                      }
                    >
                      {day}
                    </button>
                  );
                })}
              </div>
            </fieldset>
            <div>
              <label htmlFor="cm-tz" className={label}>
                Time zone
              </label>
              <input
                id="cm-tz"
                className={input}
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="cm-wait" className={label}>
                  Nudge after (minutes)
                </label>
                <input
                  id="cm-wait"
                  type="number"
                  min={0}
                  className={input}
                  value={wait}
                  onChange={(e) => setWait(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="cm-final" className={label}>
                  Give up after (minutes)
                </label>
                <input
                  id="cm-final"
                  type="number"
                  min={0}
                  className={input}
                  value={finalWait}
                  onChange={(e) => setFinalWait(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label htmlFor="cm-consent" className={label}>
                Write-back consent
              </label>
              <select
                id="cm-consent"
                className={input}
                value={nextConsent}
                onChange={(e) => setNextConsent(e.target.value as WriteBackConsent)}
              >
                <option value="always_ask">Always ask (default)</option>
                <option value="auto_apply">Auto apply</option>
                <option value="never">Never</option>
              </select>
            </div>
            <div className="flex justify-end gap-2">
              <Dialog.Close asChild>
                <Pill type="button" size="sm" variant="ghost">
                  Cancel
                </Pill>
              </Dialog.Close>
              <Pill type="submit" size="sm" disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save"}
              </Pill>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
