import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse } from "../../api/schema";
import { usePods, useProjects } from "../../app/directory";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { refusal } from "./adminErrors";
import { slugId } from "./adminWords";
import { ConfirmChange, removeButtonClass, type Pending } from "./AdminDialog";
import { EntityDialog } from "./EntityDialog";
import { nodeDetails } from "./entityForm";
import {
  ENTITY_WORDS,
  deleteImpact,
  newlyOutsideScope,
  parentsOf,
  reposOf,
  withoutKey,
  type EditableKind,
  type EntityKind,
  type RepoScope,
} from "./structure";
import { useConfigList, useLinks, useStructureChanged } from "./useStructure";

const CREATORS: Record<EditableKind, (id: string, name: string) => Promise<ConfigNodeResponse>> = {
  program: (id, name) => apiClient.createConfigProgram({ id, name }),
  project: (id, name) => apiClient.createConfigProject({ id, name }),
  pod: (id, name) => apiClient.createConfigPod({ id, name }),
  workstream: (id, name) => apiClient.createConfigWorkstream({ id, name }),
};

const DELETERS: Record<EditableKind, (id: string) => Promise<void>> = {
  program: (id) => apiClient.deleteConfigProgram(id),
  project: (id) => apiClient.deleteConfigProject(id),
  pod: (id) => apiClient.deleteConfigPod(id),
  workstream: (id) => apiClient.deleteConfigWorkstream(id),
};

/** The id prefix each kind has always used, so a new record looks like the seeded ones. */
const ID_PREFIX: Record<EditableKind, string> = {
  program: "program",
  project: "project",
  pod: "pod",
  workstream: "ws",
};

const PANELS: { kind: EntityKind; title: string; hint?: string }[] = [
  { kind: "program", title: "Programs" },
  { kind: "project", title: "Projects" },
  { kind: "pod", title: "Pods" },
  {
    kind: "workstream",
    title: "Workstreams (optional)",
    hint: "Pods are the one grouping you need. Add a workstream only when several teams share one piece of scope. It shows in Delivery and Today once a task is in it.",
  },
  { kind: "member", title: "Members" },
];

const smallButton = "h-8 px-3 text-[12px]";

/**
 * The hierarchy everything rolls up along: programs hold projects, projects are worked on by
 * pods, and pods hold people. Programs, projects, pods and workstreams can be added, changed
 * and deleted here; members come from the chat directory, and linking is on the Links tab.
 */
export function EntitiesTab() {
  const navigate = useNavigate();
  const changed = useStructureChanged();
  const lists = {
    program: useConfigList("program"),
    project: useConfigList("project"),
    pod: useConfigList("pod"),
    workstream: useConfigList("workstream"),
    member: useConfigList("member"),
  };
  const { links, inUse, error: linksError } = useLinks();
  // The directory's own lists say how many tasks a project or pod holds.
  const directoryProjects = useProjects();
  const directoryPods = usePods();
  const [editing, setEditing] = useState<{ kind: EditableKind; node: ConfigNodeResponse } | null>(
    null,
  );
  const [pending, setPending] = useState<Pending | null>(null);

  const names = new Map<string, string>();
  for (const list of Object.values(lists)) {
    for (const node of list.data ?? []) names.set(node.id, node.name);
  }
  const nameOf = (id: string) => names.get(id) ?? id;

  const scope: RepoScope | null = links
    ? {
        links,
        projectRepos: Object.fromEntries((lists.project.data ?? []).map((p) => [p.id, reposOf(p)])),
        podRepos: Object.fromEntries((lists.pod.data ?? []).map((p) => [p.id, reposOf(p)])),
      }
    : null;

  /** What deleting would change, read before the question is asked. */
  const askDelete = async (kind: EditableKind, node: ConfigNodeResponse) => {
    if (!links || !scope) return;
    const word = ENTITY_WORDS[kind].one;
    let dayReports: number | undefined;
    if (kind === "project") {
      try {
        dayReports = (await apiClient.dayReports(node.id)).length;
      } catch {
        // Not knowing is not the same as none: the line about reports is left out.
      }
    }
    // A pod that reads repositories only this project provides would fall out of scope.
    const outsideScope =
      kind === "project"
        ? newlyOutsideScope(scope, {
            ...scope,
            links: { ...links, projectPods: withoutKey(links.projectPods, node.id) },
            projectRepos: withoutKey(scope.projectRepos, node.id),
          })
        : undefined;
    const held =
      kind === "project"
        ? directoryProjects.data?.find((item) => item.id === node.id)
        : kind === "pod"
          ? directoryPods.data?.find((item) => item.id === node.id)
          : undefined;
    const impact = deleteImpact(kind, node, links, nameOf, {
      dayReports,
      outsideScope,
      tasks: held?.task_ids.length,
    });
    setPending({
      key: `delete:${node.id}:${Date.now()}`,
      title: `Delete ${node.name}?`,
      effect: `This removes the ${word} from today on and ends every link to it. Earlier days still show it. It can't be undone here.`,
      lines: impact.lines,
      warnings: impact.warnings,
      confirmLabel: `Delete ${word}`,
      run: async () => {
        await DELETERS[kind](node.id);
        toast.success(`Deleted ${node.name}.`);
        await changed();
      },
    });
  };

  return (
    <>
      <p className="mb-4 max-w-[720px] text-[13px] text-grey-body">
        Programs hold projects, projects are worked on by pods, and pods hold people. Add and change
        them here; put them together under{" "}
        <Link to="/admin?tab=links" className="font-bold">
          Links
        </Link>
        .
      </p>
      {linksError ? (
        <p className="mb-4 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red">
          Could not read how things are linked: {refusal(linksError)} Changing and deleting need it.
        </p>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
        {PANELS.map(({ kind, title, hint }) => {
          const query = lists[kind];
          const nodes = [...(query.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
          return (
            <EntityPanel
              key={kind}
              kind={kind}
              title={title}
              hint={hint}
              nodes={nodes}
              loading={query.isLoading}
              error={query.error}
              allIds={[...names.keys()]}
              noteFor={(node) => {
                if (!links) return null;
                if (kind === "project" && (lists.program.data ?? []).length > 0) {
                  return parentsOf(links.programProjects, node.id).length === 0
                    ? "Not in a program yet. Add it under Links."
                    : null;
                }
                if (kind === "pod") {
                  return parentsOf(links.projectPods, node.id).length === 0
                    ? "Not on a project yet. Add it under Links."
                    : null;
                }
                if (kind === "workstream") {
                  return inUse.has(node.id)
                    ? null
                    : "No task in it yet, so Delivery and Today don't show it.";
                }
                if (kind === "member") {
                  return parentsOf(links.podMembers, node.id).length === 0 ? "In no pod." : null;
                }
                return null;
              }}
              detailsFor={(node) =>
                nodeDetails(kind, node, {
                  people: links?.podMembers[node.id]?.length,
                })
              }
              ready={links !== null}
              onEdit={(node) => {
                if (kind !== "member") setEditing({ kind, node });
              }}
              onDelete={(node) => {
                if (kind !== "member") void askDelete(kind, node);
              }}
              onAdded={(node) =>
                toast.success(`Added ${node.name}. Next, link it under Links.`, {
                  action: { label: "Open Links", onClick: () => navigate("/admin?tab=links") },
                })
              }
            />
          );
        })}
      </div>
      {editing && scope ? (
        <EntityDialog
          key={editing.node.id}
          kind={editing.kind}
          node={editing.node}
          members={lists.member.data ?? []}
          scope={scope}
          nameOf={nameOf}
          onClose={() => setEditing(null)}
        />
      ) : null}
      <ConfirmChange pending={pending} onClose={() => setPending(null)} />
    </>
  );
}

function EntityPanel({
  kind,
  title,
  hint,
  nodes,
  loading,
  error,
  allIds,
  noteFor,
  detailsFor,
  ready,
  onEdit,
  onDelete,
  onAdded,
}: {
  kind: EntityKind;
  title: string;
  hint?: string;
  nodes: ConfigNodeResponse[];
  loading: boolean;
  error: unknown;
  /** Every id in use, whatever it is, so a new name that would collide is caught before sending. */
  allIds: string[];
  noteFor: (node: ConfigNodeResponse) => string | null;
  detailsFor: (node: ConfigNodeResponse) => string[];
  ready: boolean;
  onEdit: (node: ConfigNodeResponse) => void;
  onDelete: (node: ConfigNodeResponse) => void;
  onAdded: (node: ConfigNodeResponse) => void;
}) {
  const changed = useStructureChanged();
  const word = ENTITY_WORDS[kind];
  const editable = kind !== "member";
  const [name, setName] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const shown = filter.trim()
    ? nodes.filter((node) =>
        `${node.name} ${node.id}`.toLowerCase().includes(filter.trim().toLowerCase()),
      )
    : nodes;

  const add = async () => {
    if (!editable) return;
    const clean = name.trim();
    const id = slugId(ID_PREFIX[kind], clean);
    if (allIds.includes(id)) {
      setProblem(`There is already a record with the id ${id}. Choose a different name.`);
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const created = await CREATORS[kind](id, clean);
      setName("");
      await changed();
      onAdded(created);
    } catch (e) {
      setProblem(refusal(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title={title} note={loading ? undefined : `${nodes.length}`}>
      {hint ? <p className="mb-2 text-[12px] text-grey-secondary">{hint}</p> : null}
      <PanelState
        needs="an admin"
        isLoading={loading}
        error={error}
        isEmpty={nodes.length === 0}
        emptyText={
          kind === "workstream"
            ? "No workstreams. That is fine: pods are enough unless several teams share one piece of scope."
            : kind === "member"
              ? "No members yet. Import people from the chat directory."
              : `No ${word.many} yet. Add the first one below.`
        }
      >
        {nodes.length > 8 ? (
          <div className="mb-2">
            <label htmlFor={`filter-${kind}`} className="sr-only">
              Filter {word.many}
            </label>
            <input
              id={`filter-${kind}`}
              className="h-9 w-full rounded-full border border-grey-border px-3 text-[13px]"
              placeholder={`Filter ${word.many}`}
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
        ) : null}
        <ul className="grid max-h-[26rem] overflow-y-auto">
          {shown.map((node) => {
            const details = detailsFor(node);
            const note = noteFor(node);
            return (
              <li
                key={node.id}
                className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 border-t border-grey-border py-2.5 first:border-t-0"
              >
                <div className="min-w-0 flex-1">
                  {kind === "member" ? (
                    <p className="text-[14px]">
                      <span className="font-bold">{node.name}</span>
                      {details.length > 0 ? (
                        <span className="text-grey-secondary"> · {details.join(" · ")}</span>
                      ) : null}
                    </p>
                  ) : (
                    <>
                      <Link
                        to={`/delivery/${kind}/${node.id}`}
                        className="text-[14px] font-bold text-ink no-underline hover:underline"
                      >
                        {node.name}
                      </Link>
                      {details.length > 0 ? (
                        <p className="mt-0.5 text-[12px] text-grey-body">{details.join(" · ")}</p>
                      ) : null}
                    </>
                  )}
                  <p className="mt-0.5 font-mono text-[11px] text-grey-secondary">{node.id}</p>
                  {note ? <p className="mt-0.5 text-[12px] text-rag-amber">{note}</p> : null}
                </div>
                {editable ? (
                  <div className="flex flex-none gap-1.5">
                    <Pill
                      size="sm"
                      variant="ghost"
                      className={smallButton}
                      disabled={!ready}
                      aria-label={`Change ${node.name}`}
                      onClick={() => onEdit(node)}
                    >
                      Change
                    </Pill>
                    <Pill
                      size="sm"
                      variant="ghost"
                      className={cn(smallButton, removeButtonClass)}
                      disabled={!ready}
                      aria-label={`Delete ${node.name}`}
                      onClick={() => onDelete(node)}
                    >
                      Delete
                    </Pill>
                  </div>
                ) : null}
              </li>
            );
          })}
          {shown.length === 0 ? (
            <li className="py-2 text-[13px] text-grey-secondary">Nothing matches that.</li>
          ) : null}
        </ul>
      </PanelState>
      {editable ? (
        <form
          className="mt-3 grid gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim() && !busy) void add();
          }}
        >
          <div className="flex gap-2">
            <label htmlFor={`new-${kind}`} className="sr-only">
              New {word.one} name
            </label>
            <input
              id={`new-${kind}`}
              className="h-9 min-w-0 flex-1 rounded-full border border-grey-border px-3 text-[13px]"
              placeholder={`New ${word.one} name`}
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setProblem(null);
              }}
            />
            <Pill type="submit" size="sm" disabled={!name.trim() || busy}>
              {busy ? "Adding…" : "Add"}
            </Pill>
          </div>
          {problem ? (
            <p role="alert" className="text-[12px] font-bold text-rag-red">
              {problem}
            </p>
          ) : null}
        </form>
      ) : (
        <p className="mt-3 text-[12px] text-grey-secondary">
          Members come from the chat directory.{" "}
          <Link to="/admin?tab=directory" className="font-bold">
            Import people under Directory
          </Link>
          .
        </p>
      )}
    </Panel>
  );
}
