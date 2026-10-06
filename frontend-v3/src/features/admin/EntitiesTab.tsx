import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { slugId } from "./adminWords";

type Kind = "program" | "project" | "workstream" | "pod" | "member";

const KINDS: { kind: Kind; label: string; list: () => Promise<ConfigNodeResponse[]> }[] = [
  { kind: "program", label: "Programs", list: () => apiClient.configPrograms() },
  { kind: "project", label: "Projects", list: () => apiClient.configProjects() },
  { kind: "workstream", label: "Workstreams", list: () => apiClient.configWorkstreams() },
  { kind: "pod", label: "Pods", list: () => apiClient.configPods() },
  { kind: "member", label: "Members", list: () => apiClient.configMembers() },
];

const CREATE: Partial<Record<Kind, (name: string) => Promise<ConfigNodeResponse>>> = {
  program: (name) => apiClient.createConfigProgram({ id: slugId("program", name), name }),
  project: (name) => apiClient.createConfigProject({ id: slugId("project", name), name }),
  workstream: (name) => apiClient.createConfigWorkstream({ id: slugId("ws", name), name }),
  pod: (name) => apiClient.createConfigPod({ id: slugId("pod", name), name }),
};

/**
 * The hierarchy everything rolls up along. Programs, projects, workstreams and
 * pods can be added here; members come from the chat directory, and linking
 * nodes together is done under Links in the console.
 */
export function EntitiesTab() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
      {KINDS.map((k) => (
        <EntityList key={k.kind} kind={k.kind} label={k.label} list={k.list} />
      ))}
    </div>
  );
}

function EntityList({
  kind,
  label,
  list,
}: {
  kind: Kind;
  label: string;
  list: () => Promise<ConfigNodeResponse[]>;
}) {
  const queryClient = useQueryClient();
  const items = useQuery({ queryKey: ["config", "entities", kind], queryFn: list });
  const [name, setName] = useState("");
  const create = CREATE[kind];
  const add = useMutation({
    mutationFn: () => create!(name.trim()),
    onSuccess: (node) => {
      toast.success(`Added ${node.name}. Link it under Links in the console.`);
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void queryClient.invalidateQueries({ queryKey: ["directory"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Panel title={label} note={items.data ? `${items.data.length}` : undefined}>
      <PanelState
        needs="an admin"
        isLoading={items.isLoading}
        error={items.error}
        isEmpty={(items.data ?? []).length === 0}
        emptyText={`No ${label.toLowerCase()} yet.`}
      >
        <ul className="grid max-h-64 gap-1 overflow-y-auto">
          {(items.data ?? []).map((node) => (
            <li
              key={node.id}
              className="flex items-baseline justify-between gap-3 border-t border-grey-border py-1.5 first:border-t-0"
            >
              {kind === "member" ? (
                <span className="text-[14px] font-bold">{node.name}</span>
              ) : (
                <Link
                  to={`/delivery/${kind}/${node.id}`}
                  className="text-[14px] font-bold text-ink no-underline hover:underline"
                >
                  {node.name}
                </Link>
              )}
              <span className="truncate text-[11px] text-grey-secondary">
                {node.code ?? node.jira_project_key ?? node.id}
              </span>
            </li>
          ))}
        </ul>
      </PanelState>
      {create ? (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) add.mutate();
          }}
        >
          <label htmlFor={`new-${kind}`} className="sr-only">
            New {kind} name
          </label>
          <input
            id={`new-${kind}`}
            className="h-9 min-w-0 flex-1 rounded-full border border-grey-border px-3 text-[13px]"
            placeholder={`New ${kind} name`}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Pill type="submit" size="sm" disabled={!name.trim() || add.isPending}>
            Add
          </Pill>
        </form>
      ) : (
        <p className="mt-3 text-[12px] text-grey-secondary">
          Members are imported from the chat directory in the console (Admin → Directory).
        </p>
      )}
    </Panel>
  );
}
