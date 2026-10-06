import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse, PodTaskDto } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { cn } from "../../lib/utils";
import { refusal } from "./adminErrors";
import { ConfirmChange, fieldInput, type Pending } from "./AdminDialog";
import { metaText } from "./entityForm";
import { AddControl, Chip, ChipList, SelectField, type Option } from "./LinkParts";
import {
  POD_ROLES,
  linkDone,
  listNames,
  newlyOutsideScope,
  parentsOf,
  plural,
  podRoleLabel,
  reposOf,
  scopeWarning,
  suggestPodRole,
  unlinkWords,
  withoutLink,
  type LinkKind,
  type Links,
  type RepoScope,
} from "./structure";
import {
  useConfigList,
  useKnownTasks,
  useLinks,
  usePodRoles,
  useStructureChanged,
} from "./useStructure";

/** What every section needs: the links, the names, and how to make or take away one. */
type Ctx = {
  links: Links;
  scope: RepoScope;
  nameOf: (id: string) => string;
  taskName: (id: string) => string;
  roles: Record<string, Record<string, string | null>>;
  tasks: PodTaskDto[];
  inUse: Set<string>;
  /** Workstreams whose own links are still being read, and those that could not be. */
  reading: Set<string>;
  unreadable: Map<string, unknown>;
  programs: ConfigNodeResponse[];
  projects: ConfigNodeResponse[];
  pods: ConfigNodeResponse[];
  workstreams: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
  /** Makes a link; throws when the server refuses, so the control that asked can say why. */
  link: (
    kind: LinkKind,
    parent: string,
    child: string,
    run: () => Promise<unknown>,
  ) => Promise<void>;
  /** Asks first, saying what taking the link away does, then does it. */
  remove: (
    kind: LinkKind,
    parent: string,
    child: string,
    run: () => Promise<unknown>,
    warnings?: string[],
  ) => void;
};

const byName = (a: ConfigNodeResponse, b: ConfigNodeResponse) => a.name.localeCompare(b.name);

/** "Kai Thompson · Backend Engineer": a title tells two people with one name apart. */
const withTitle = (member: ConfigNodeResponse): string => {
  const title = metaText(member, "title");
  return title ? `${member.name} · ${title}` : member.name;
};

/**
 * What is linked to what, and the way to change it. Each section is one kind of link, with
 * what is linked now shown as chips, so nothing has to be remembered to be removed. Every
 * removal says what else it changes before it is done.
 */
export function LinksTab() {
  const programs = useConfigList("program");
  const projects = useConfigList("project");
  const pods = useConfigList("pod");
  const workstreams = useConfigList("workstream");
  const members = useConfigList("member");
  const { links, inUse, isLoading, error, reading, unreadable } = useLinks();
  const podIds = (pods.data ?? []).map((pod) => pod.id);
  const roles = usePodRoles(podIds);
  const { tasks } = useKnownTasks(podIds);
  const changed = useStructureChanged();
  const [pending, setPending] = useState<Pending | null>(null);

  const lists = [programs, projects, pods, workstreams, members];
  const loading = isLoading || lists.some((query) => query.isLoading);
  const failure = error ?? lists.find((query) => query.error)?.error ?? null;

  const names = new Map<string, string>();
  for (const query of lists) for (const node of query.data ?? []) names.set(node.id, node.name);
  const nameOf = (id: string) => names.get(id) ?? id;
  const taskName = (id: string) => tasks.find((task) => task.id === id)?.name ?? id;
  // A task has no config node of its own: its name comes from the pods that hold it.
  const childName = (kind: LinkKind, id: string) =>
    kind === "member-task" || kind === "workstream-task" ? taskName(id) : nameOf(id);

  const ready = links !== null && !loading && !failure;
  const ctx: Ctx | null = links
    ? {
        links,
        scope: {
          links,
          projectRepos: Object.fromEntries((projects.data ?? []).map((p) => [p.id, reposOf(p)])),
          podRepos: Object.fromEntries((pods.data ?? []).map((p) => [p.id, reposOf(p)])),
        },
        nameOf,
        taskName,
        roles,
        tasks,
        inUse,
        reading,
        unreadable,
        programs: [...(programs.data ?? [])].sort(byName),
        projects: [...(projects.data ?? [])].sort(byName),
        pods: [...(pods.data ?? [])].sort(byName),
        workstreams: [...(workstreams.data ?? [])].sort(byName),
        members: [...(members.data ?? [])].sort(byName),
        link: async (kind, parent, child, run) => {
          await run();
          toast.success(linkDone(kind, nameOf(parent), childName(kind, child), true));
          await changed();
        },
        remove: (kind, parent, child, run, warnings = []) => {
          const parentName = nameOf(parent);
          const words = unlinkWords(kind, parentName, childName(kind, child));
          setPending({
            key: `${kind}:${parent}:${child}:${Date.now()}`,
            title: words.title,
            effect: words.effect,
            warnings,
            confirmLabel: "Remove link",
            run: async () => {
              await run();
              toast.success(linkDone(kind, parentName, childName(kind, child), false));
              await changed();
            },
          });
        },
      }
    : null;

  return (
    <PanelState needs="an admin" isLoading={loading} error={failure} onRetry={() => void changed()}>
      {ready && ctx ? (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          <p className="max-w-[720px] text-[13px] text-grey-body">
            What is linked to what, from the program down to each person. Take a link away with its
            ×; you are told what else changes before anything is done. Add the things themselves
            under{" "}
            <Link to="/admin?tab=entities" className="font-bold">
              Entities
            </Link>
            .
          </p>
          <ProgramProjects ctx={ctx} />
          <PodsOnProjects ctx={ctx} />
          <PeopleInPods ctx={ctx} />
          <WorkstreamLinks ctx={ctx} />
          <TaskAssignment ctx={ctx} />
        </div>
      ) : null}
      <ConfirmChange pending={pending} onClose={() => setPending(null)} />
    </PanelState>
  );
}

function Section({
  title,
  note,
  empty,
  collapsed,
  children,
}: {
  title: string;
  note: string;
  /** Said instead of the rows when there is nothing to link them to. */
  empty?: string | null;
  /** Keeps the rows folded away until opened, for a part nobody has to set up. */
  collapsed?: string;
  children: ReactNode;
}) {
  return (
    <Panel title={title}>
      <p className="mb-2 max-w-[720px] text-[12px] text-grey-secondary">{note}</p>
      {empty ? (
        <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">{empty}</p>
      ) : collapsed ? (
        <details className="rounded-2xl border border-grey-border">
          <summary className="cursor-pointer select-none px-4 py-3 text-[13px] font-bold">
            {collapsed}
          </summary>
          <ul className="grid px-4">{children}</ul>
        </details>
      ) : (
        <ul className="grid">{children}</ul>
      )}
    </Panel>
  );
}

/** One parent and what is linked to it: its name on the left, the chips and the add control right. */
function LinkRow({ title, to, children }: { title: string; to?: string; children: ReactNode }) {
  return (
    <li className="grid gap-2 border-t border-grey-border py-3 first:border-t-0 md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)] md:gap-4">
      <div className="min-w-0">
        {to ? (
          <Link to={to} className="text-[14px] font-bold text-ink no-underline hover:underline">
            {title}
          </Link>
        ) : (
          <span className="text-[14px] font-bold">{title}</span>
        )}
      </div>
      <div className="grid min-w-0 gap-2">{children}</div>
    </li>
  );
}

// ---- Programs and projects ----------------------------------------------------------------------------

function ProgramProjects({ ctx }: { ctx: Ctx }) {
  const { links, programs, projects, nameOf } = ctx;
  return (
    <Section
      title="Projects in a program"
      note="A project sits in one program. Its status rolls up into the program's, and the portfolio views start from the program."
      empty={programs.length === 0 ? "No programs yet. Add one under Entities first." : null}
    >
      {programs.map((program) => {
        const inside = links.programProjects[program.id] ?? [];
        const options: Option[] = projects
          .filter((project) => !inside.includes(project.id))
          .map((project) => {
            const elsewhere = parentsOf(links.programProjects, project.id);
            return elsewhere.length > 0
              ? {
                  value: project.id,
                  label: `${project.name} (in ${listNames(elsewhere.map(nameOf))})`,
                  disabled: true,
                }
              : { value: project.id, label: project.name };
          });
        return (
          <LinkRow key={program.id} title={program.name} to={`/delivery/program/${program.id}`}>
            <ChipList
              empty="No projects in it yet."
              items={inside.map((id) => (
                <Chip
                  key={id}
                  label={nameOf(id)}
                  removeLabel={`Take ${nameOf(id)} out of ${program.name}`}
                  onRemove={() =>
                    ctx.remove("program-project", program.id, id, () =>
                      apiClient.unlinkProjectProgram(id, program.id),
                    )
                  }
                />
              ))}
            />
            <AddControl
              id={`add-project-${program.id}`}
              label={`Add a project to ${program.name}`}
              placeholder="Add a project…"
              nothingLeft="Every project is in a program"
              options={options}
              onAdd={(projectId) =>
                ctx.link("program-project", program.id, projectId, () =>
                  apiClient.linkProjectProgram(projectId, { program_id: program.id }),
                )
              }
            />
          </LinkRow>
        );
      })}
    </Section>
  );
}

function PodsOnProjects({ ctx }: { ctx: Ctx }) {
  const { links, projects, pods, nameOf, scope } = ctx;
  return (
    <Section
      title="Pods on a project"
      note="A pod's check-ins, blockers and tasks count toward every project it is on. A pod can be on more than one."
      empty={
        projects.length === 0
          ? "No projects yet. Add one under Entities first."
          : pods.length === 0
            ? "No pods yet. Add one under Entities first."
            : null
      }
    >
      {projects.map((project) => {
        const on = links.projectPods[project.id] ?? [];
        const options: Option[] = pods
          .filter((pod) => !on.includes(pod.id))
          .map((pod) => ({ value: pod.id, label: pod.name }));
        return (
          <LinkRow key={project.id} title={project.name} to={`/delivery/project/${project.id}`}>
            <ChipList
              empty="No pods on it yet."
              items={on.map((id) => {
                // Taking a pod off a project shrinks the repositories the pod may read.
                const after: RepoScope = {
                  ...scope,
                  links: {
                    ...links,
                    projectPods: withoutLink(links.projectPods, project.id, id),
                  },
                };
                const warnings = newlyOutsideScope(scope, after).map((item) =>
                  scopeWarning(nameOf(item.podId), item.repos),
                );
                return (
                  <Chip
                    key={id}
                    label={nameOf(id)}
                    removeLabel={`Take ${nameOf(id)} off ${project.name}`}
                    onRemove={() =>
                      ctx.remove(
                        "project-pod",
                        project.id,
                        id,
                        () => apiClient.unlinkPodProject(id, project.id),
                        warnings,
                      )
                    }
                  />
                );
              })}
            />
            <AddControl
              id={`add-pod-${project.id}`}
              label={`Put a pod on ${project.name}`}
              placeholder="Add a pod…"
              nothingLeft="Every pod is on this project"
              options={options}
              onAdd={(podId) =>
                ctx.link("project-pod", project.id, podId, () =>
                  apiClient.linkPodProject(podId, project.id),
                )
              }
            />
          </LinkRow>
        );
      })}
    </Section>
  );
}

// ---- People in pods ---------------------------------------------------------------------------------------

function PeopleInPods({ ctx }: { ctx: Ctx }) {
  const { links, pods, nameOf, roles } = ctx;
  return (
    <Section
      title="People in a pod"
      note="A person's check-in rolls up into the pod they are in. Someone in no pod shows as in no team. The role is a label: it puts the right person first when you pick the pod's escalation contacts."
      empty={pods.length === 0 ? "No pods yet. Add one under Entities first." : null}
    >
      {pods.map((pod) => {
        const inside = links.podMembers[pod.id] ?? [];
        return (
          <LinkRow key={pod.id} title={pod.name} to={`/delivery/pod/${pod.id}`}>
            <ChipList
              empty="Nobody in it yet."
              items={inside.map((id) => (
                <Chip
                  key={id}
                  label={nameOf(id)}
                  note={podRoleLabel(roles[pod.id]?.[id]) || undefined}
                  removeLabel={`Take ${nameOf(id)} out of ${pod.name}`}
                  onRemove={() =>
                    ctx.remove("pod-member", pod.id, id, () =>
                      apiClient.unlinkPodMember(pod.id, id),
                    )
                  }
                />
              ))}
            />
            <AddPerson pod={pod} ctx={ctx} />
          </LinkRow>
        );
      })}
    </Section>
  );
}

function AddPerson({ pod, ctx }: { pod: ConfigNodeResponse; ctx: Ctx }) {
  const [role, setRole] = useState("developer");
  const inside = ctx.links.podMembers[pod.id] ?? [];
  const options: Option[] = ctx.members
    .filter((member) => !inside.includes(member.id))
    .map((member) => ({ value: member.id, label: withTitle(member) }));
  return (
    <AddControl
      id={`add-person-${pod.id}`}
      label={`Add a person to ${pod.name}`}
      placeholder="Add a person…"
      nothingLeft="Everyone is in this pod"
      options={options}
      onSelect={(memberId) => {
        const member = ctx.members.find((item) => item.id === memberId);
        if (member) setRole(suggestPodRole(member.metadata.app_roles));
      }}
      onAdd={(memberId) =>
        ctx.link("pod-member", pod.id, memberId, () =>
          apiClient.linkPodMember(pod.id, memberId, { role }),
        )
      }
    >
      {() => (
        <SelectField
          id={`add-role-${pod.id}`}
          label={`Their role in ${pod.name}`}
          value={role}
          onChange={setRole}
          options={POD_ROLES.map((item) => ({ value: item.value, label: item.label }))}
        />
      )}
    </AddControl>
  );
}

// ---- Workstreams, optional -------------------------------------------------------------------------

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 md:grid-cols-[7.5rem_minmax(0,1fr)] md:gap-3">
      <p className="pt-1.5 text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        {label}
      </p>
      <div className="grid min-w-0 gap-2">{children}</div>
    </div>
  );
}

function WorkstreamLinks({ ctx }: { ctx: Ctx }) {
  const { links, workstreams, projects, pods, nameOf, taskName, inUse } = ctx;
  return (
    <Section
      title="Workstreams (optional)"
      note="Skip this unless several teams share one piece of scope: pods are enough otherwise. A workstream appears in Delivery and Today once a task is in it."
      empty={
        workstreams.length === 0
          ? "No workstreams. That is fine: pods are enough on their own."
          : null
      }
      collapsed={`Show ${plural(workstreams.length, "workstream")} and what each is linked to`}
    >
      {workstreams.map((ws) => {
        const inProjects = parentsOf(links.projectWorkstreams, ws.id);
        const byPods = parentsOf(links.podWorkstreams, ws.id);
        const taskIds = links.workstreamTasks[ws.id] ?? [];
        return (
          <li key={ws.id} className="grid gap-3 border-t border-grey-border py-3 first:border-t-0">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                to={`/delivery/workstream/${ws.id}`}
                className="text-[14px] font-bold text-ink no-underline hover:underline"
              >
                {ws.name}
              </Link>
              {inUse.has(ws.id) ? null : (
                <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                  empty: hidden from Delivery and Today
                </RagChip>
              )}
            </div>
            {ctx.reading.has(ws.id) ? (
              <p className="text-[13px] text-grey-secondary">Reading what it is linked to…</p>
            ) : ctx.unreadable.has(ws.id) ? (
              <p role="alert" className="text-[13px] text-rag-red">
                Could not read what it is linked to: {refusal(ctx.unreadable.get(ws.id))}
              </p>
            ) : (
              <>
                <Group label="Project">
                  <ChipList
                    empty="Not in a project yet."
                    items={inProjects.map((id) => (
                      <Chip
                        key={id}
                        label={nameOf(id)}
                        removeLabel={`Take ${ws.name} out of ${nameOf(id)}`}
                        onRemove={() =>
                          ctx.remove("project-workstream", id, ws.id, () =>
                            apiClient.unlinkProjectWorkstream(id, ws.id),
                          )
                        }
                      />
                    ))}
                  />
                  <AddControl
                    id={`add-ws-project-${ws.id}`}
                    label={`Put ${ws.name} in a project`}
                    placeholder="Add to a project…"
                    nothingLeft="It is in every project"
                    options={projects
                      .filter((project) => !inProjects.includes(project.id))
                      .map((project) => ({ value: project.id, label: project.name }))}
                    onAdd={(projectId) =>
                      ctx.link("project-workstream", projectId, ws.id, () =>
                        apiClient.linkProjectWorkstream(projectId, ws.id),
                      )
                    }
                  />
                </Group>
                <Group label="Pods">
                  <ChipList
                    empty="No pod works on it yet."
                    items={byPods.map((id) => (
                      <Chip
                        key={id}
                        label={nameOf(id)}
                        removeLabel={`Stop ${nameOf(id)} working on ${ws.name}`}
                        onRemove={() =>
                          ctx.remove("pod-workstream", id, ws.id, () =>
                            apiClient.unlinkPodWorkstream(id, ws.id),
                          )
                        }
                      />
                    ))}
                  />
                  <AddControl
                    id={`add-ws-pod-${ws.id}`}
                    label={`Have a pod work on ${ws.name}`}
                    placeholder="Add a pod…"
                    nothingLeft="Every pod works on it"
                    options={pods
                      .filter((pod) => !byPods.includes(pod.id))
                      .map((pod) => ({ value: pod.id, label: pod.name }))}
                    onAdd={(podId) =>
                      ctx.link("pod-workstream", podId, ws.id, () =>
                        apiClient.linkPodWorkstream(podId, ws.id),
                      )
                    }
                  />
                </Group>
                <Group label="Tasks">
                  <ChipList
                    empty="No task in it yet."
                    items={taskIds.map((id) => (
                      <Chip
                        key={id}
                        label={taskName(id)}
                        note={taskName(id) === id ? undefined : id}
                        removeLabel={`Take ${taskName(id)} out of ${ws.name}`}
                        onRemove={() =>
                          ctx.remove("workstream-task", ws.id, id, () =>
                            apiClient.unlinkWorkstreamTask(ws.id, id),
                          )
                        }
                      />
                    ))}
                  />
                  <AddTask
                    id={`add-ws-task-${ws.id}`}
                    label={`Put a task in ${ws.name}`}
                    tasks={ctx.tasks.filter((task) => !taskIds.includes(task.id))}
                    onAdd={(taskId) =>
                      ctx.link("workstream-task", ws.id, taskId, () =>
                        apiClient.linkWorkstreamTask(ws.id, taskId),
                      )
                    }
                  />
                </Group>
              </>
            )}
          </li>
        );
      })}
    </Section>
  );
}

// ---- Task assignment ---------------------------------------------------------------------------------------

/** A task id typed or picked from the ones the pods hold; an id nobody lists is still tried. */
function AddTask({
  id,
  label,
  tasks,
  onAdd,
  buttonLabel = "Add",
}: {
  id: string;
  label: string;
  tasks: PodTaskDto[];
  onAdd: (taskId: string) => Promise<void>;
  buttonLabel?: string;
}) {
  const [taskId, setTaskId] = useState("");
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    try {
      await onAdd(taskId.trim());
      setTaskId("");
    } catch (error) {
      toast.error(refusal(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (taskId.trim() && !busy) void add();
      }}
    >
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        list={`${id}-tasks`}
        className={cn(fieldInput, "w-64 max-w-full")}
        placeholder={tasks[0] ? `Task id, for example ${tasks[0].id}` : "Task id"}
        autoComplete="off"
        value={taskId}
        onChange={(event) => setTaskId(event.target.value)}
      />
      <datalist id={`${id}-tasks`}>
        {tasks.map((task) => (
          <option key={task.id} value={task.id}>
            {task.name}
          </option>
        ))}
      </datalist>
      <Pill type="submit" size="sm" variant="dark" disabled={!taskId.trim() || busy}>
        {busy ? "Adding…" : buttonLabel}
      </Pill>
    </form>
  );
}

function TaskAssignment({ ctx }: { ctx: Ctx }) {
  const { members, tasks, nameOf } = ctx;
  const [memberId, setMemberId] = useState("");
  const theirs = tasks.filter((task) => task.owners.some((owner) => owner.id === memberId));
  return (
    <Section
      title="Task assignment"
      note="The Jira sync assigns each task to the person whose Jira account it names, set under Directory. Use this to correct one by hand. If Jira still names someone, the next sync assigns it to them again."
      empty={members.length === 0 ? "No members yet. Import people under Directory first." : null}
    >
      <li className="grid gap-3 py-1">
        <SelectField
          id="assign-person"
          label="Whose tasks"
          placeholder="Choose a person…"
          value={memberId}
          onChange={setMemberId}
          options={members.map((member) => ({ value: member.id, label: withTitle(member) }))}
        />
        {memberId ? (
          <>
            <ChipList
              empty={`${nameOf(memberId)} has no task in any pod.`}
              items={theirs.map((task) => (
                <Chip
                  key={task.id}
                  label={task.name}
                  note={task.id}
                  removeLabel={`Take ${task.name} off ${nameOf(memberId)}`}
                  onRemove={() =>
                    ctx.remove("member-task", memberId, task.id, () =>
                      apiClient.unassignMemberTask(memberId, task.id),
                    )
                  }
                />
              ))}
            />
            <AddTask
              id="assign-task"
              label={`Assign a task to ${nameOf(memberId)}`}
              tasks={tasks.filter((task) => !theirs.some((mine) => mine.id === task.id))}
              buttonLabel="Assign"
              onAdd={(taskId) =>
                ctx.link("member-task", memberId, taskId, () =>
                  apiClient.assignMemberTask(memberId, { task_id: taskId }),
                )
              }
            />
            <p className="text-[12px] text-grey-secondary">
              Tasks come from Jira. An id that does not exist is refused.
            </p>
          </>
        ) : null}
      </li>
    </Section>
  );
}
