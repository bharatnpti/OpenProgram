import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { apiClient } from "../api/client";
import type {
  CheckinPreferenceResponse,
  ConfigNodeResponse,
  DirectoryUserResponse,
} from "../api/schema";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Dialog } from "../components/ui/dialog";
import { Field } from "../components/ui/field";
import { Input } from "../components/ui/input";
import { Select } from "../components/ui/select";
import { Slider } from "../components/ui/slider";
import { Textarea } from "../components/ui/textarea";

const nodeSchema = z.object({
  id: z.string().min(1, "ID is required"),
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
  code: z.string().optional(),
});

type NodeFormValues = z.infer<typeof nodeSchema>;

type EntityKind = "programs" | "projects" | "pods" | "members";

const entityLabels: Record<EntityKind, string> = {
  programs: "Programs",
  projects: "Projects",
  pods: "Pods",
  members: "Members",
};

const weekdayOptions = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
];

export function AdminConfigPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<EntityKind>("programs");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigNodeResponse | null>(null);
  const [directoryDialogOpen, setDirectoryDialogOpen] = useState(false);
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directoryDebouncedQuery, setDirectoryDebouncedQuery] = useState("");
  const [directoryOffset, setDirectoryOffset] = useState(0);
  const [selectedDirectoryIds, setSelectedDirectoryIds] = useState<string[]>([]);

  const programs = useQuery({ queryKey: ["config", "programs"], queryFn: apiClient.configPrograms });
  const projects = useQuery({ queryKey: ["config", "projects"], queryFn: apiClient.configProjects });
  const pods = useQuery({ queryKey: ["config", "pods"], queryFn: apiClient.configPods });
  const members = useQuery({ queryKey: ["config", "members"], queryFn: apiClient.configMembers });
  const checkinPreferences = useQuery({
    queryKey: ["config", "checkin-preferences"],
    queryFn: apiClient.configCheckinPreferences,
  });

  const entitiesByTab: Record<EntityKind, ConfigNodeResponse[] | undefined> = {
    programs: programs.data,
    projects: projects.data,
    pods: pods.data,
    members: members.data,
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDirectoryDebouncedQuery(directoryQuery);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [directoryQuery]);

  useEffect(() => {
    setDirectoryOffset(0);
  }, [directoryDebouncedQuery]);

  useEffect(() => {
    if (!directoryDialogOpen) {
      setSelectedDirectoryIds([]);
    }
  }, [directoryDialogOpen]);

  const form = useForm<NodeFormValues>({
    resolver: zodResolver(nodeSchema),
    defaultValues: { id: "", name: "", description: "", code: "" },
  });

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["config"] }),
      queryClient.invalidateQueries({ queryKey: ["directory"] }),
    ]);
  };

  const directorySearch = useQuery({
    queryKey: ["directory", directoryDebouncedQuery, directoryOffset],
    queryFn: () => apiClient.searchDirectory(directoryDebouncedQuery, 25, directoryOffset),
    enabled: directoryDialogOpen,
  });

  const directorySyncMutation = useMutation({
    mutationFn: apiClient.syncDirectory,
    onSuccess: invalidateAll,
  });

  const addDirectoryMembersMutation = useMutation({
    mutationFn: (externalIds: string[]) => apiClient.addMembersFromDirectory(externalIds),
    onSuccess: async () => {
      setDirectoryDialogOpen(false);
      setSelectedDirectoryIds([]);
      await invalidateAll();
    },
  });

  const saveMutation = useMutation({
    mutationFn: async (values: NodeFormValues) => {
      const payload = {
        id: values.id,
        name: values.name,
        description: values.description || null,
        code: values.code || null,
      };
      if (editing) {
        if (activeTab === "programs") {
          return apiClient.updateConfigProgram(editing.id, payload);
        }
        if (activeTab === "projects") {
          return apiClient.updateConfigProject(editing.id, payload);
        }
        if (activeTab === "pods") {
          return apiClient.updateConfigPod(editing.id, payload);
        }
        return apiClient.updateConfigMember(editing.id, payload);
      }
      if (activeTab === "programs") {
        return apiClient.createConfigProgram(payload);
      }
      if (activeTab === "projects") {
        return apiClient.createConfigProject(payload);
      }
      if (activeTab === "pods") {
        return apiClient.createConfigPod(payload);
      }
      return apiClient.createConfigMember(payload);
    },
    onSuccess: async () => {
      setDialogOpen(false);
      setEditing(null);
      form.reset();
      await invalidateAll();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      if (activeTab === "programs") return apiClient.deleteConfigProgram(id);
      if (activeTab === "projects") return apiClient.deleteConfigProject(id);
      if (activeTab === "pods") return apiClient.deleteConfigPod(id);
      return apiClient.deleteConfigMember(id);
    },
    onSuccess: invalidateAll,
  });

  const openCreate = () => {
    setEditing(null);
    form.reset({ id: "", name: "", description: "", code: "" });
    setDialogOpen(true);
  };

  const openEdit = (node: ConfigNodeResponse) => {
    setEditing(node);
    form.reset({
      id: node.id,
      name: node.name,
      description: node.description ?? "",
      code: node.code ?? "",
    });
    setDialogOpen(true);
  };

  const currentEntities = entitiesByTab[activeTab] ?? [];
  const directoryItems = directorySearch.data?.items ?? [];
  const directoryTotal = directorySearch.data?.total ?? 0;
  const pageSize = 25;

  const toggleDirectorySelection = (id: string) => {
    setSelectedDirectoryIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  return (
    <main className="px-5 py-5">
      <header className="mb-4 border-b border-border pb-4">
        <h1 className="text-xl font-semibold">Admin Configuration</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage runtime programs, projects, pods, members, links, and check-in timing.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap gap-2">
        {(Object.keys(entityLabels) as EntityKind[]).map((tab) => (
          <Button
            key={tab}
            type="button"
            className={activeTab === tab ? "border-primary bg-primary/10 text-primary" : ""}
            onClick={() => setActiveTab(tab)}
          >
            {entityLabels[tab]}
          </Button>
        ))}
      </div>

      <section className="rounded border border-border bg-white">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{entityLabels[activeTab]}</h2>
          <div className="flex flex-wrap gap-2">
            {activeTab === "members" && (
              <>
                <Button type="button" onClick={() => setDirectoryDialogOpen(true)}>
                  Add from directory
                </Button>
                <Button
                  type="button"
                  onClick={() => directorySyncMutation.mutate()}
                  disabled={directorySyncMutation.isPending}
                >
                  Sync directory
                </Button>
              </>
            )}
            <Button type="button" onClick={openCreate}>
              Add {entityLabels[activeTab].slice(0, -1)}
            </Button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">ID</th>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium">Code</th>
                <th className="px-4 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {currentEntities.map((node) => (
                <tr key={node.id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-2 font-mono text-xs">{node.id}</td>
                  <td className="px-4 py-2">{node.name}</td>
                  <td className="px-4 py-2 text-muted-foreground">{node.description ?? "-"}</td>
                  <td className="px-4 py-2">{node.code ?? "-"}</td>
                  <td className="px-4 py-2">
                    <div className="flex gap-2">
                      <Button type="button" onClick={() => openEdit(node)}>
                        Edit
                      </Button>
                      <Button
                        type="button"
                        onClick={() => deleteMutation.mutate(node.id)}
                        disabled={deleteMutation.isPending}
                      >
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {currentEntities.length === 0 && (
            <p className="px-4 py-4 text-sm text-muted-foreground">No records yet.</p>
          )}
        </div>
      </section>

      <RelationshipPanel
        programs={programs.data ?? []}
        projects={projects.data ?? []}
        pods={pods.data ?? []}
        members={members.data ?? []}
        onChanged={invalidateAll}
      />

      <CheckinPreferencesPanel
        members={members.data ?? []}
        preferences={checkinPreferences.data ?? []}
        onChanged={invalidateAll}
      />

      <Dialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        title={editing ? `Edit ${entityLabels[activeTab].slice(0, -1)}` : `Create ${entityLabels[activeTab].slice(0, -1)}`}
      >
        <form
          className="space-y-3"
          onSubmit={form.handleSubmit((values) => saveMutation.mutate(values))}
        >
          <Field label="ID" htmlFor="node-id" error={form.formState.errors.id?.message}>
            <Input id="node-id" disabled={Boolean(editing)} {...form.register("id")} />
          </Field>
          <Field label="Name" htmlFor="node-name" error={form.formState.errors.name?.message}>
            <Input id="node-name" {...form.register("name")} />
          </Field>
          <Field label="Description" htmlFor="node-description">
            <Textarea id="node-description" {...form.register("description")} />
          </Field>
          {(activeTab === "projects" || editing?.code) && (
            <Field label="Code" htmlFor="node-code">
              <Input id="node-code" {...form.register("code")} />
            </Field>
          )}
          {saveMutation.isError && (
            <p className="text-xs text-red-600">Save failed. Check IDs and permissions.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saveMutation.isPending}>
              Save
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={directoryDialogOpen}
        onOpenChange={setDirectoryDialogOpen}
        title="Add members from directory"
      >
        <div className="space-y-4">
          <Field label="Search directory" htmlFor="directory-search">
            <Input
              id="directory-search"
              placeholder="Search by name, email, or handle"
              value={directoryQuery}
              onChange={(event) => setDirectoryQuery(event.target.value)}
            />
          </Field>

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {directorySearch.isFetching ? "Searching..." : `${directoryTotal} matches`}
            </span>
            <span>{selectedDirectoryIds.length} selected</span>
          </div>

          <div className="max-h-[28rem] overflow-y-auto rounded border border-border">
            {directoryItems.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted-foreground">
                {directorySearch.isFetching ? "Loading directory users..." : "No matches."}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {directoryItems.map((item) => (
                  <li key={item.external_id} className="px-4 py-3">
                    <label className="flex cursor-pointer items-center gap-3">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-border"
                        checked={selectedDirectoryIds.includes(item.external_id)}
                        onChange={() => toggleDirectorySelection(item.external_id)}
                      />
                      <DirectoryUserAvatar user={item} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{item.display_name}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {item.email ?? item.handle ?? item.external_id}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-2">
                          {item.title && <Badge tone="info">{item.title}</Badge>}
                          {item.handle && <Badge tone="neutral">@{item.handle}</Badge>}
                        </div>
                      </div>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-2">
              <Button
                type="button"
                onClick={() => setDirectoryOffset((current) => Math.max(0, current - pageSize))}
                disabled={directoryOffset === 0 || directorySearch.isFetching}
              >
                Previous
              </Button>
              <Button
                type="button"
                onClick={() => setDirectoryOffset((current) => current + pageSize)}
                disabled={directoryOffset + pageSize >= directoryTotal || directorySearch.isFetching}
              >
                Next
              </Button>
            </div>
            <div className="flex gap-2">
              <Button type="button" onClick={() => setDirectoryDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => addDirectoryMembersMutation.mutate(selectedDirectoryIds)}
                disabled={
                  selectedDirectoryIds.length === 0 || addDirectoryMembersMutation.isPending
                }
              >
                Add selected
              </Button>
            </div>
          </div>

          {directorySearch.isError && (
            <p className="text-xs text-red-600">Search failed. Try syncing the directory again.</p>
          )}
          {addDirectoryMembersMutation.isError && (
            <p className="text-xs text-red-600">Add failed. Verify the selected users still exist.</p>
          )}
        </div>
      </Dialog>
    </main>
  );
}

function RelationshipPanel({
  programs,
  projects,
  pods,
  members,
  onChanged,
}: {
  programs: ConfigNodeResponse[];
  projects: ConfigNodeResponse[];
  pods: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
  onChanged: () => Promise<void>;
}) {
  const [projectId, setProjectId] = useState("");
  const [programId, setProgramId] = useState("");
  const [podId, setPodId] = useState("");
  const [linkProjectId, setLinkProjectId] = useState("");
  const [memberId, setMemberId] = useState("");
  const [memberRole, setMemberRole] = useState("developer");
  const [taskMemberId, setTaskMemberId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>, message: string) => {
    try {
      await action();
      setStatus(message);
      await onChanged();
    } catch {
      setStatus("Operation failed.");
    }
  };

  return (
    <section className="mt-4 rounded border border-border bg-white">
      <div className="border-b border-border px-4 py-3 text-sm font-semibold">Links & assignments</div>
      <div className="grid gap-4 px-4 py-4 lg:grid-cols-2">
        <LinkForm
          title="Project → Program"
          onSubmit={() =>
            run(
              () => apiClient.linkProjectProgram(projectId, { program_id: programId }),
              "Linked project to program.",
            )
          }
          onUnlink={() =>
            run(
              () => apiClient.unlinkProjectProgram(projectId, programId),
              "Unlinked project from program.",
            )
          }
        >
          <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            <option value="">Select project</option>
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Select value={programId} onChange={(e) => setProgramId(e.target.value)}>
            <option value="">Select program</option>
            {programs.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </LinkForm>

        <LinkForm
          title="Pod → Project"
          onSubmit={() =>
            run(
              () => apiClient.linkPodProject(podId, linkProjectId),
              "Linked pod to project.",
            )
          }
          onUnlink={() =>
            run(
              () => apiClient.unlinkPodProject(podId, linkProjectId),
              "Unlinked pod from project.",
            )
          }
        >
          <Select value={podId} onChange={(e) => setPodId(e.target.value)}>
            <option value="">Select pod</option>
            {pods.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Select value={linkProjectId} onChange={(e) => setLinkProjectId(e.target.value)}>
            <option value="">Select project</option>
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </LinkForm>

        <LinkForm
          title="Pod → Member"
          onSubmit={() =>
            run(
              () => apiClient.linkPodMember(podId, memberId, { role: memberRole }),
              "Linked member to pod.",
            )
          }
          onUnlink={() =>
            run(() => apiClient.unlinkPodMember(podId, memberId), "Unlinked member from pod.")
          }
        >
          <Select value={podId} onChange={(e) => setPodId(e.target.value)}>
            <option value="">Select pod</option>
            {pods.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Select value={memberId} onChange={(e) => setMemberId(e.target.value)}>
            <option value="">Select member</option>
            {members.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Input
            placeholder="Role in pod"
            value={memberRole}
            onChange={(e) => setMemberRole(e.target.value)}
          />
        </LinkForm>

        <LinkForm
          title="Member → Task"
          onSubmit={() =>
            run(
              () => apiClient.assignMemberTask(taskMemberId, { task_id: taskId }),
              "Assigned task to member.",
            )
          }
          onUnlink={() =>
            run(
              () => apiClient.unassignMemberTask(taskMemberId, taskId),
              "Unassigned task from member.",
            )
          }
        >
          <Select value={taskMemberId} onChange={(e) => setTaskMemberId(e.target.value)}>
            <option value="">Select member</option>
            {members.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Input
            placeholder="Task ID"
            value={taskId}
            onChange={(e) => setTaskId(e.target.value)}
          />
        </LinkForm>
      </div>
      {status && <p className="px-4 pb-4 text-xs text-muted-foreground">{status}</p>}
    </section>
  );
}

function LinkForm({
  title,
  children,
  onSubmit,
  onUnlink,
}: {
  title: string;
  children: ReactNode;
  onSubmit: () => void;
  onUnlink: () => void;
}) {
  return (
    <div className="space-y-2 rounded border border-border p-3">
      <div className="text-sm font-medium">{title}</div>
      <div className="space-y-2">{children}</div>
      <div className="flex gap-2">
        <Button type="button" onClick={onSubmit}>
          Link
        </Button>
        <Button type="button" onClick={onUnlink}>
          Unlink
        </Button>
      </div>
    </div>
  );
}

function CheckinPreferencesPanel({
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

  const toggleWeekday = (day: number) => {
    setWeekdays((current) =>
      current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort(),
    );
  };

  const save = async () => {
    if (!memberId) return;
    await apiClient.updateConfigMemberCheckinPreference(memberId, {
      local_time: localTime,
      timezone,
      weekdays,
      reply_wait_seconds: replyWait,
      final_reply_wait_seconds: finalReplyWait,
    });
    await onChanged();
  };

  return (
    <section className="mt-4 rounded border border-border bg-white">
      <div className="border-b border-border px-4 py-3 text-sm font-semibold">
        Check-in preferences
      </div>
      <div className="grid gap-4 px-4 py-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-3">
          <Field label="Member" htmlFor="checkin-member">
            <Select
              id="checkin-member"
              value={memberId}
              onChange={(event) => {
                const nextMember = event.target.value;
                setMemberId(nextMember);
                const pref = preferences.find((item) => item.developer_id === nextMember);
                if (pref) {
                  setLocalTime(pref.local_time.slice(0, 5));
                  setTimezone(pref.timezone ?? "UTC");
                  setWeekdays(pref.weekdays);
                  setReplyWait(pref.reply_wait_seconds);
                  setFinalReplyWait(pref.final_reply_wait_seconds);
                }
              }}
            >
              <option value="">Select member</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.name}
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Local time" htmlFor="checkin-time">
              <Input
                id="checkin-time"
                type="time"
                value={localTime}
                onChange={(event) => setLocalTime(event.target.value)}
              />
            </Field>
            <Field label="Timezone" htmlFor="checkin-timezone">
              <Input
                id="checkin-timezone"
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              />
            </Field>
          </div>
          <Field label="Weekdays">
            <div className="flex flex-wrap gap-2">
              {weekdayOptions.map((day) => (
                <button
                  key={day.value}
                  type="button"
                  className={`rounded border px-2 py-1 text-xs ${
                    weekdays.includes(day.value)
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground"
                  }`}
                  onClick={() => toggleWeekday(day.value)}
                >
                  {day.label}
                </button>
              ))}
            </div>
          </Field>
          <Slider
            label="Reply wait (seconds)"
            min={60}
            max={3600}
            step={60}
            value={replyWait}
            valueLabel={`${replyWait}s`}
            onChange={(event) => setReplyWait(Number(event.target.value))}
          />
          <Slider
            label="Final reply wait (seconds)"
            min={60}
            max={7200}
            step={60}
            value={finalReplyWait}
            valueLabel={`${finalReplyWait}s`}
            onChange={(event) => setFinalReplyWait(Number(event.target.value))}
          />
          <Button type="button" onClick={() => void save()} disabled={!memberId}>
            Save preference
          </Button>
        </div>
        <div>
          <div className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
            Configured preferences
          </div>
          <div className="divide-y divide-border rounded border border-border">
            {preferences.length === 0 && (
              <p className="px-3 py-3 text-sm text-muted-foreground">No preferences yet.</p>
            )}
            {preferences.map((pref) => (
              <div key={pref.developer_id} className="px-3 py-2 text-sm">
                <div className="font-medium">{pref.developer_id}</div>
                <div className="text-xs text-muted-foreground">
                  {pref.local_time} / {pref.weekdays.join(",")}
                </div>
                <Badge tone="info">{pref.reply_wait_seconds}s wait</Badge>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
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
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
      {initials || "?"}
    </div>
  );
}
