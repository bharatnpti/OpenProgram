import { useMutation, useQuery } from "@tanstack/react-query";
import { DatabaseZap, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { DirectoryUserResponse } from "../../api/schema";
import { errorMessage } from "./adminTypes";

const PAGE_SIZE = 25;

export function DirectoryPanel({ onChanged }: { onChanged: () => Promise<void> }) {
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directoryDebouncedQuery, setDirectoryDebouncedQuery] = useState("");
  const [directoryOffset, setDirectoryOffset] = useState(0);
  const [selectedDirectoryIds, setSelectedDirectoryIds] = useState<string[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDirectoryDebouncedQuery(directoryQuery), 300);
    return () => window.clearTimeout(timer);
  }, [directoryQuery]);

  useEffect(() => {
    setDirectoryOffset(0);
  }, [directoryDebouncedQuery]);

  const directorySearch = useQuery({
    queryKey: ["directory", directoryDebouncedQuery, directoryOffset],
    queryFn: () => apiClient.searchDirectory(directoryDebouncedQuery, PAGE_SIZE, directoryOffset),
  });
  const directoryItems = directorySearch.data?.items ?? [];
  const directoryTotal = directorySearch.data?.total ?? 0;

  const directorySyncMutation = useMutation({
    mutationFn: apiClient.syncDirectory,
    onSuccess: async (data) => {
      await onChanged();
      toast.success(
        `Directory synced: ${data.synced_count} synced, ${data.deactivated_count} deactivated.`,
      );
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const addDirectoryMembersMutation = useMutation({
    mutationFn: (externalIds: string[]) => apiClient.addMembersFromDirectory(externalIds),
    onSuccess: async (data) => {
      setSelectedDirectoryIds([]);
      await onChanged();
      toast.success(`Added ${data.length} member${data.length === 1 ? "" : "s"}.`);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  function toggleSelection(id: string) {
    setSelectedDirectoryIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">Directory import</h2>
          <p className="mt-1 text-[13px] text-grey-secondary">
            Search synced users and promote selected people into configured members.
          </p>
        </div>
        <Pill
          variant="ghost"
          size="sm"
          disabled={directorySyncMutation.isPending}
          onClick={() => directorySyncMutation.mutate()}
        >
          <DatabaseZap size={14} />
          {directorySyncMutation.isPending ? "Syncing…" : "Sync directory"}
        </Pill>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <TextInput
          value={directoryQuery}
          onChange={(event) => setDirectoryQuery(event.target.value)}
          placeholder="Search by name, email, or handle"
          className="max-w-sm"
        />
        <RagChip tone="neutral">
          {directorySearch.isFetching ? "Searching" : `${directoryTotal} matches`}
        </RagChip>
        <RagChip tone="info">{selectedDirectoryIds.length} selected</RagChip>
      </div>

      {directorySearch.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(directorySearch.error)}</p>
      ) : directoryItems.length === 0 ? (
        <div className="rounded-3xl border border-grey-border bg-white px-5 py-10 text-center text-[14px] text-grey-secondary">
          {directorySearch.isFetching ? "Loading users…" : "No directory matches."}
        </div>
      ) : (
        <div className="max-h-[32rem] overflow-y-auto rounded-3xl border border-grey-border bg-white">
          {directoryItems.map((item, index) => (
            <label
              key={item.external_id}
              className={
                "flex cursor-pointer items-center gap-3 px-5 py-3" +
                (index === 0 ? "" : " border-t border-grey-fill")
              }
            >
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-grey-border accent-magenta"
                checked={selectedDirectoryIds.includes(item.external_id)}
                onChange={() => toggleSelection(item.external_id)}
              />
              <DirectoryUserAvatar user={item} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[15px] font-bold">{item.display_name}</div>
                <div className="truncate text-[13px] text-grey-secondary">
                  {item.email ?? item.handle ?? item.external_id}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {item.title && <RagChip tone="info">{item.title}</RagChip>}
                  {item.handle && <RagChip tone="neutral">@{item.handle}</RagChip>}
                </div>
              </div>
            </label>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-2.5">
          <Pill
            variant="ghost"
            size="sm"
            disabled={directoryOffset === 0 || directorySearch.isFetching}
            onClick={() => setDirectoryOffset((current) => Math.max(0, current - PAGE_SIZE))}
          >
            Previous
          </Pill>
          <Pill
            variant="ghost"
            size="sm"
            disabled={directoryOffset + PAGE_SIZE >= directoryTotal || directorySearch.isFetching}
            onClick={() => setDirectoryOffset((current) => current + PAGE_SIZE)}
          >
            Next
          </Pill>
        </div>
        <Pill
          variant="primary"
          size="sm"
          disabled={selectedDirectoryIds.length === 0 || addDirectoryMembersMutation.isPending}
          onClick={() => addDirectoryMembersMutation.mutate(selectedDirectoryIds)}
        >
          <UserPlus size={14} />
          Add selected
        </Pill>
      </div>
    </div>
  );
}

function DirectoryUserAvatar({ user }: { user: DirectoryUserResponse }) {
  const initials = user.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  if (user.avatar_url) {
    return (
      <img
        src={user.avatar_url}
        alt=""
        className="h-10 w-10 shrink-0 rounded-full object-cover"
      />
    );
  }
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-magenta-tint text-[13px] font-bold text-magenta">
      {initials || "?"}
    </div>
  );
}
