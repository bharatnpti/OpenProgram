import { keepPreviousData, useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse, DirectoryUserResponse } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { refusal } from "./adminErrors";
import { ConfirmChange, fieldInput, type Pending } from "./AdminDialog";
import { IdentityDialog } from "./IdentityDialog";
import {
  autoMatchSummary,
  gapWords,
  identityGaps,
  importSummary,
  stamp,
  syncSummary,
} from "./identityForm";
import { listNames, plural } from "./structure";
import { useConfigList, useStructureChanged } from "./useStructure";

const SEARCH_PAGE = 20;
const TABLE_PAGE = 15;

const HEALTH: Record<string, { tone: BadgeTone; word: string }> = {
  healthy: { tone: "success", word: "healthy" },
  stale: { tone: "warning", word: "stale" },
  failing: { tone: "danger", word: "failing" },
  never_synced: { tone: "neutral", word: "never synced" },
  not_configured: { tone: "neutral", word: "not configured" },
  disabled: { tone: "neutral", word: "switched off" },
};

/**
 * People: sync the chat directory, choose who becomes a member, and say which chat, Jira and Git
 * accounts each member is. Nobody is imported unless someone ticked them and confirmed the names.
 */
export function DirectoryTab() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <SyncCard />
      <ImportCard />
      <IdentityCard />
    </div>
  );
}

// ---- The directory and its sync -----------------------------------------------------------------------

function SyncCard() {
  const changed = useStructureChanged();
  const status = useQuery({
    queryKey: ["admin", "sync-status"],
    queryFn: () => apiClient.syncStatus(),
  });
  const source = status.data?.sources.find((item) => item.source === "directory");
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);

  const sync = useMutation({
    mutationFn: () => apiClient.syncDirectory(),
    onSuccess: async (result) => {
      setOutcome({ ok: true, text: syncSummary(result) });
      await changed();
    },
    onError: (error) => setOutcome({ ok: false, text: `Could not sync: ${refusal(error)}` }),
  });

  const health = source ? HEALTH[source.health] : undefined;
  return (
    <Panel
      title="Chat directory"
      note={
        health ? (
          <RagChip tone={health.tone} className="h-6 px-2.5 text-[12px]">
            {health.word}
          </RagChip>
        ) : undefined
      }
    >
      <p className="max-w-[720px] text-[13px] text-grey-body">
        The people in your chat workspace. Syncing refreshes that list so you can choose who joins;
        it does not add or change any member.
      </p>
      {source ? (
        <p className="mt-2 text-[12px] text-grey-secondary">
          {source.provider}
          {source.simulated ? " (simulated)" : ""} · last synced {stamp(source.last_synced_at)}
          {source.last_error ? ` · last error: ${source.last_error}` : ""}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Pill size="sm" variant="dark" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? "Syncing…" : "Sync the directory now"}
        </Pill>
        {outcome ? (
          <p
            role="status"
            className={outcome.ok ? "text-[13px] text-grey-body" : "text-[13px] text-rag-red"}
          >
            {outcome.text}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

// ---- Choosing who joins ---------------------------------------------------------------------------------

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "?"
  );
}

function ImportCard() {
  const changed = useStructureChanged();
  const members = useConfigList("member");
  const memberIds = new Set((members.data ?? []).map((member) => member.id));
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  // Keyed by chat id. Whoever is ticked stays ticked across searches and pages.
  const [picked, setPicked] = useState<Map<string, DirectoryUserResponse>>(new Map());
  const [pending, setPending] = useState<Pending | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(text.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text]);

  const search = useQuery({
    queryKey: ["config", "directory-users", query, offset],
    queryFn: () => apiClient.searchDirectory(query, SEARCH_PAGE, offset),
    placeholderData: keepPreviousData,
  });
  const people = search.data?.items ?? [];
  const total = search.data?.total ?? 0;

  const toggle = (user: DirectoryUserResponse) =>
    setPicked((current) => {
      const next = new Map(current);
      if (next.has(user.external_id)) next.delete(user.external_id);
      else next.set(user.external_id, user);
      return next;
    });

  const askImport = () => {
    const chosen = [...picked.values()];
    const some =
      chosen.length === 1 ? "This person becomes a member" : "These people become members";
    setPending({
      key: `import:${Date.now()}`,
      title: `Import ${plural(chosen.length, "person", "people")} as members?`,
      effect: `${some}. Members are asked to check in by direct message on their check-in days, so import only the people who work with this team.`,
      names: chosen.map(
        (user) => `${user.display_name} · ${user.email ?? user.handle ?? user.external_id}`,
      ),
      confirmLabel: `Import ${plural(chosen.length, "person", "people")}`,
      destructive: false,
      run: async () => {
        await apiClient.addMembersFromDirectory(chosen.map((user) => user.external_id));
        const already = chosen.filter((user) => memberIds.has(user.external_id)).length;
        setPicked(new Map());
        toast.success(importSummary(chosen.length - already, already));
        await changed();
      },
    });
  };

  return (
    <Panel title="Import people">
      <p className="max-w-[720px] text-[13px] text-grey-body">
        Tick the people who should become members. Nobody is ticked for you, and you see every name
        before anything is imported. A member is asked to check in by direct message.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor="directory-search" className="sr-only">
          Search the directory
        </label>
        <input
          id="directory-search"
          className={`${fieldInput} max-w-xs`}
          placeholder="Search by name, email or handle"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
          {search.isFetching ? "Searching…" : plural(total, "person", "people")}
        </RagChip>
        <RagChip tone={picked.size > 0 ? "info" : "neutral"} className="h-6 px-2.5 text-[12px]">
          {picked.size} ticked
        </RagChip>
        {picked.size > 0 ? (
          <button
            type="button"
            className="text-[12px] font-bold text-magenta hover:underline"
            onClick={() => setPicked(new Map())}
          >
            Clear
          </button>
        ) : null}
      </div>
      <div className="mt-3">
        <PanelState
          needs="an admin"
          isLoading={search.isLoading}
          error={search.error}
          onRetry={() => void search.refetch()}
          isEmpty={people.length === 0}
          emptyText={
            query
              ? "Nobody in the directory matches that."
              : "The directory is empty. Sync it above to read the people in your chat workspace."
          }
        >
          <ul className="max-h-[28rem] overflow-y-auto rounded-2xl border border-grey-border">
            {people.map((user) => {
              const isMember = memberIds.has(user.external_id);
              const left = !user.is_active;
              const locked = isMember || left;
              return (
                <li key={user.external_id} className="border-t border-grey-border first:border-t-0">
                  <label
                    className={`flex items-center gap-3 px-4 py-2.5 ${locked ? "opacity-70" : "cursor-pointer hover:bg-grey-header"}`}
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 flex-none accent-magenta"
                      checked={picked.has(user.external_id)}
                      disabled={locked}
                      onChange={() => toggle(user)}
                    />
                    <span
                      aria-hidden
                      className="grid h-9 w-9 flex-none place-items-center rounded-full bg-magenta-tint text-[12px] font-bold text-magenta"
                    >
                      {initials(user.display_name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[14px] font-bold">
                        {user.display_name}
                      </span>
                      <span className="block truncate text-[12px] text-grey-secondary">
                        {[user.title, user.email ?? (user.handle ? `@${user.handle}` : null)]
                          .filter(Boolean)
                          .join(" · ") || user.external_id}
                      </span>
                    </span>
                    {isMember ? (
                      <RagChip tone="success" className="h-6 px-2.5 text-[12px]">
                        member
                      </RagChip>
                    ) : left ? (
                      <RagChip tone="warning" className="h-6 px-2.5 text-[12px]">
                        left the directory
                      </RagChip>
                    ) : null}
                  </label>
                </li>
              );
            })}
          </ul>
        </PanelState>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-2">
          <Pill
            size="sm"
            variant="ghost"
            disabled={offset === 0 || search.isFetching}
            onClick={() => setOffset(Math.max(0, offset - SEARCH_PAGE))}
          >
            Previous
          </Pill>
          <Pill
            size="sm"
            variant="ghost"
            disabled={offset + SEARCH_PAGE >= total || search.isFetching}
            onClick={() => setOffset(offset + SEARCH_PAGE)}
          >
            Next
          </Pill>
        </div>
        <Pill size="sm" disabled={picked.size === 0} onClick={askImport}>
          {picked.size === 0
            ? "Import the people you tick"
            : `Review ${plural(picked.size, "person", "people")} to import`}
        </Pill>
      </div>
      <ConfirmChange pending={pending} onClose={() => setPending(null)} />
    </Panel>
  );
}

// ---- Which accounts are whom -------------------------------------------------------------------------------

function IdentityCard() {
  const changed = useStructureChanged();
  const members = useConfigList("member");
  const unmapped = useQuery({
    queryKey: ["config", "unmapped-members"],
    queryFn: () => apiClient.configUnmappedMembers(),
  });
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<ConfigNodeResponse | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [filled, setFilled] = useState<string | null>(null);

  const all = [...(members.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const matching = filter.trim()
    ? all.filter((member) =>
        `${member.name} ${member.id}`.toLowerCase().includes(filter.trim().toLowerCase()),
      )
    : all;
  const pages = Math.max(1, Math.ceil(matching.length / TABLE_PAGE));
  const current = Math.min(page, pages - 1);
  const rows = matching.slice(current * TABLE_PAGE, (current + 1) * TABLE_PAGE);

  const links = useQueries({
    queries: rows.map((member) => ({
      queryKey: ["config", "member-identity-link", member.id],
      queryFn: () => apiClient.configMemberIdentityLink(member.id),
    })),
  });

  const askAutoMatch = () =>
    setPending({
      key: `auto-match:${Date.now()}`,
      title: "Fill in the accounts the directory knows?",
      effect:
        "For each member, fills in a missing chat id and Jira email from the directory, then finds the Jira account from that email. Anything already set stays as it is.",
      confirmLabel: "Fill in",
      destructive: false,
      run: async () => {
        const result = await apiClient.autoMatchConfigIdentityLinks();
        setFilled(autoMatchSummary(result));
        toast.success(
          result.updated_count === 0
            ? "Nothing needed filling in."
            : `Filled in ${plural(result.updated_count, "member")}.`,
        );
        await changed();
      },
    });

  const stuck = unmapped.data ?? [];
  return (
    <Panel
      title="Chat, Jira and Git accounts"
      note={
        <Pill size="sm" variant="ghost" onClick={askAutoMatch}>
          Fill in from the directory
        </Pill>
      }
    >
      <p className="max-w-[720px] text-[13px] text-grey-body">
        Which chat, Jira and Git account is each member. The chat id is how the bot reaches them;
        the Jira account is how their issues are found; the Git username is how their commits are.
      </p>
      <div className="mt-2 text-[13px]" aria-live="polite">
        {unmapped.data ? (
          stuck.length === 0 ? (
            <p className="text-rag-green">Every member can be reached and matched.</p>
          ) : (
            <p className="rounded-2xl bg-rag-amber-bg px-4 py-3 text-rag-amber-deep">
              {plural(stuck.length, "member")} cannot be reached or matched yet:{" "}
              {listNames(
                stuck.map((member) => `${member.name} (${gapWords(member.missing)})`),
                4,
              )}
              .
            </p>
          )
        ) : null}
        {filled ? <p className="mt-2 text-grey-body">{filled}</p> : null}
      </div>
      <div className="mt-3">
        <PanelState
          needs="an admin"
          isLoading={members.isLoading}
          error={members.error}
          isEmpty={all.length === 0}
          emptyText="No members yet. Tick people in the list above and import them."
        >
          {all.length > TABLE_PAGE ? (
            <div className="mb-2">
              <label htmlFor="identity-filter" className="sr-only">
                Filter members
              </label>
              <input
                id="identity-filter"
                className="h-9 w-full max-w-xs rounded-full border border-grey-border px-3 text-[13px]"
                placeholder="Filter members"
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value);
                  setPage(0);
                }}
              />
            </div>
          ) : null}
          <TableBox>
            <table className="w-full min-w-[820px] border-collapse">
              <thead>
                <tr>
                  <th className={th}>Member</th>
                  <th className={th}>Chat id</th>
                  <th className={th}>Jira account</th>
                  <th className={th}>Jira email</th>
                  <th className={th}>Git username</th>
                  <th className={th} />
                </tr>
              </thead>
              <tbody>
                {rows.map((member, index) => {
                  const link = links[index];
                  const data = link?.data;
                  const gaps = data ? identityGaps(data) : [];
                  const cell = (value: string | null | undefined) =>
                    link?.isLoading ? (
                      <span className="text-grey-secondary">…</span>
                    ) : value ? (
                      <span className="font-mono text-[12px]">{value}</span>
                    ) : (
                      <span className="text-grey-secondary">—</span>
                    );
                  return (
                    <tr key={member.id}>
                      <td className={td}>
                        <span className="font-bold">{member.name}</span>
                        <span className="block font-mono text-[11px] text-grey-secondary">
                          {member.id}
                        </span>
                        {gaps.length > 0 ? (
                          <span
                            className={`block text-[12px] ${gaps.includes("chat_user_id") ? "text-rag-red" : "text-rag-amber"}`}
                          >
                            Missing: {gapWords(gaps)}
                          </span>
                        ) : null}
                        {link?.error ? (
                          <span className="block text-[12px] text-rag-red">
                            {refusal(link.error)}
                          </span>
                        ) : null}
                      </td>
                      <td className={td}>{cell(data?.chat_user_id)}</td>
                      <td className={td}>{cell(data?.jira_account_id)}</td>
                      <td className={td}>{cell(data?.jira_email)}</td>
                      <td className={td}>{cell(data?.vcs_username)}</td>
                      <td className={`${td} text-right`}>
                        <Pill
                          size="sm"
                          variant="ghost"
                          className="h-8 px-3 text-[12px]"
                          aria-label={`Change the accounts for ${member.name}`}
                          onClick={() => setEditing(member)}
                        >
                          Change
                        </Pill>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableBox>
          {pages > 1 ? (
            <div className="mt-3 flex items-center gap-2">
              <Pill
                size="sm"
                variant="ghost"
                disabled={current === 0}
                onClick={() => setPage(current - 1)}
              >
                Previous
              </Pill>
              <span className="text-[12px] text-grey-secondary">
                Page {current + 1} of {pages}
              </span>
              <Pill
                size="sm"
                variant="ghost"
                disabled={current >= pages - 1}
                onClick={() => setPage(current + 1)}
              >
                Next
              </Pill>
            </div>
          ) : null}
        </PanelState>
      </div>
      <p className="mt-3 text-[12px] text-grey-secondary">
        A pod's escalation contacts are picked from members with a chat id.{" "}
        <Link to="/admin?tab=contacts" className="font-bold">
          Set escalation contacts
        </Link>
        .
      </p>
      {editing ? <IdentityDialog member={editing} onClose={() => setEditing(null)} /> : null}
      <ConfirmChange pending={pending} onClose={() => setPending(null)} />
    </Panel>
  );
}
