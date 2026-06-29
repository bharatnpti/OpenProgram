import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseZap, Link2, Settings2, Trash2, UserPlus } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { apiClient } from "../api/client";
import type {
  CheckinPreferenceResponse,
  ConfigNodeResponse,
  DirectoryUserResponse,
} from "../api/schema";
import { DataTable, type DataTableColumn } from "../components/ops/DataTable";
import {
  DataPanel,
  EmptyState,
  ErrorState,
  KpiCard,
  PageHeader,
  QueryState,
  SearchInput,
} from "../components/ops/primitives";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { ConfirmDialog } from "../components/ui/confirm-dialog";
import { Dialog } from "../components/ui/dialog";
import { Field } from "../components/ui/field";
import { Input } from "../components/ui/input";
import { Select } from "../components/ui/select";
import { Slider } from "../components/ui/slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Textarea } from "../components/ui/textarea";

const nodeSchema = z.object({
  id: z.string().min(1, "ID is required"),
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
  code: z.string().optional(),
  jira_project_key: z.string().optional(),
  jira_base_jql: z.string().optional(),
  jira_board_id: z.string().optional(),
  jira_filter_jql: z.string().optional(),
  github_repos: z.string().optional(),
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

type ConfirmState =
  | {
      open: true;
      title: string;
      description: string;
      confirmLabel: string;
      destructive?: boolean;
      onConfirm: () => void;
    }
  | { open: false };

export function AdminConfigPage() {
  const queryClient = useQueryClient();
  const [activeEntity, setActiveEntity] = useState<EntityKind>("programs");
  const [entityQuery, setEntityQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigNodeResponse | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState>({ open: false });

  const programs = useQuery({
    queryKey: ["config", "programs"],
    queryFn: apiClient.configPrograms,
  });
  const projects = useQuery({
    queryKey: ["config", "projects"],
    queryFn: apiClient.configProjects,
  });
  const pods = useQuery({ queryKey: ["config", "pods"], queryFn: apiClient.configPods });
  const members = useQuery({ queryKey: ["config", "members"], queryFn: apiClient.configMembers });
  const checkinPreferences = useQuery({
    queryKey: ["config", "checkin-preferences"],
    queryFn: apiClient.configCheckinPreferences,
  });

  const entityQueryByTab = {
    programs,
    projects,
    pods,
    members,
  }[activeEntity];

  const form = useForm<NodeFormValues>({
    resolver: zodResolver(nodeSchema),
    defaultValues: defaultNodeValues(),
  });

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["config"] }),
      queryClient.invalidateQueries({ queryKey: ["directory"] }),
    ]);
  };

  const saveMutation = useMutation({
    mutationFn: (values: NodeFormValues) => saveNode(activeEntity, values, editing),
    onSuccess: async () => {
      setDialogOpen(false);
      setEditing(null);
      form.reset(defaultNodeValues());
      await invalidateAll();
      toast.success("Configuration saved.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: ({ kind, id }: { kind: EntityKind; id: string }) => deleteNode(kind, id),
    onSuccess: async () => {
      await invalidateAll();
      toast.success("Record deleted.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const currentEntities = useMemo(
    () =>
      filterNodes(
        {
          programs: programs.data,
          projects: projects.data,
          pods: pods.data,
          members: members.data,
        }[activeEntity] ?? [],
        entityQuery,
      ),
    [activeEntity, entityQuery, members.data, pods.data, programs.data, projects.data],
  );

  const columns: DataTableColumn<ConfigNodeResponse>[] = [
    {
      accessorKey: "id",
      header: "ID",
      cell: ({ row }) => <span className="font-mono text-xs">{row.original.id}</span>,
    },
    {
      accessorKey: "name",
      header: "Name",
      cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
    },
    {
      accessorKey: "description",
      header: "Description",
      cell: ({ row }) => (
        <span className="text-muted-foreground">{row.original.description ?? "-"}</span>
      ),
    },
    {
      accessorKey: "code",
      header: "Code",
      cell: ({ row }) => row.original.code ?? "-",
    },
    {
      id: "integrations",
      header: "Integrations",
      cell: ({ row }) => <IntegrationSummary node={row.original} />,
    },
    {
      id: "actions",
      header: "Actions",
      enableSorting: false,
      cell: ({ row }) => (
        <div className="flex gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => openEdit(row.original)}>
            Edit
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-danger hover:text-danger"
            disabled={deleteMutation.isPending}
            onClick={() => confirmDelete(activeEntity, row.original)}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </Button>
        </div>
      ),
    },
  ];

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow="Admin"
          title="Runtime Configuration"
          description="Manage hierarchy, directory onboarding, graph links, assignments, and check-in timing."
        />

        <section className="grid gap-3 md:grid-cols-4">
          <KpiCard label="Programs" value={programs.data?.length ?? "-"} tone="info" />
          <KpiCard label="Projects" value={projects.data?.length ?? "-"} tone="info" />
          <KpiCard label="Pods" value={pods.data?.length ?? "-"} tone="info" />
          <KpiCard label="Members" value={members.data?.length ?? "-"} tone="info" />
        </section>

        <Tabs defaultValue="entities">
          <TabsList className="flex w-full flex-wrap justify-start">
            <TabsTrigger value="entities">
              <Settings2 className="mr-2 h-4 w-4" />
              Entities
            </TabsTrigger>
            <TabsTrigger value="relationships">
              <Link2 className="mr-2 h-4 w-4" />
              Links
            </TabsTrigger>
            <TabsTrigger value="directory">
              <UserPlus className="mr-2 h-4 w-4" />
              Directory
            </TabsTrigger>
            <TabsTrigger value="preferences">
              <DatabaseZap className="mr-2 h-4 w-4" />
              Check-ins
            </TabsTrigger>
          </TabsList>

          <TabsContent value="entities">
            <DataPanel
              title="Configured Entities"
              description="Create and maintain the program, project, pod, and member nodes used by the Graph of Truth."
              action={
                <Button type="button" variant="primary" onClick={openCreate}>
                  Add {entityLabels[activeEntity].slice(0, -1)}
                </Button>
              }
            >
              <div className="mb-4 flex flex-wrap items-center gap-2">
                {(Object.keys(entityLabels) as EntityKind[]).map((kind) => (
                  <Button
                    key={kind}
                    type="button"
                    size="sm"
                    variant={activeEntity === kind ? "primary" : "outline"}
                    onClick={() => setActiveEntity(kind)}
                  >
                    {entityLabels[kind]}
                  </Button>
                ))}
                <SearchInput
                  value={entityQuery}
                  onChange={setEntityQuery}
                  placeholder={`Search ${entityLabels[activeEntity].toLowerCase()}`}
                  className="min-w-64"
                />
              </div>
              <QueryState query={entityQueryByTab}>
                {() => (
                  <DataTable
                    data={currentEntities}
                    columns={columns}
                    emptyTitle="No records"
                    emptyDescription="Create a record or adjust the search filter."
                  />
                )}
              </QueryState>
            </DataPanel>
          </TabsContent>

          <TabsContent value="relationships">
            <RelationshipPanel
              programs={programs.data ?? []}
              projects={projects.data ?? []}
              pods={pods.data ?? []}
              members={members.data ?? []}
              onChanged={invalidateAll}
              askConfirm={setConfirm}
            />
          </TabsContent>

          <TabsContent value="directory">
            <DirectoryPanel onChanged={invalidateAll} />
          </TabsContent>

          <TabsContent value="preferences">
            <CheckinPreferencesPanel
              members={members.data ?? []}
              preferences={checkinPreferences.data ?? []}
              onChanged={invalidateAll}
            />
          </TabsContent>
        </Tabs>

        <Dialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          title={
            editing
              ? `Edit ${entityLabels[activeEntity].slice(0, -1)}`
              : `Create ${entityLabels[activeEntity].slice(0, -1)}`
          }
          description="IDs are stable graph identifiers. Editing an existing ID is disabled."
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
            {(activeEntity === "projects" || editing?.code) && (
              <Field label="Code" htmlFor="node-code">
                <Input id="node-code" {...form.register("code")} />
              </Field>
            )}
            {activeEntity === "projects" && (
              <div className="space-y-3 rounded-md border border-border bg-surface-muted/30 p-3">
                <div className="text-sm font-semibold">Project Integrations</div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="Jira project key" htmlFor="project-jira-key">
                    <Input id="project-jira-key" {...form.register("jira_project_key")} />
                  </Field>
                  <Field label="Jira board ID" htmlFor="project-jira-board">
                    <Input id="project-jira-board" {...form.register("jira_board_id")} />
                  </Field>
                </div>
                <Field label="Jira base JQL" htmlFor="project-jira-jql">
                  <Textarea id="project-jira-jql" {...form.register("jira_base_jql")} />
                </Field>
                <Field label="GitHub repos" htmlFor="project-github-repos">
                  <Textarea id="project-github-repos" {...form.register("github_repos")} />
                </Field>
              </div>
            )}
            {activeEntity === "pods" && (
              <div className="space-y-3 rounded-md border border-border bg-surface-muted/30 p-3">
                <div className="text-sm font-semibold">Pod Integrations</div>
                <Field label="Jira filter JQL" htmlFor="pod-jira-filter">
                  <Textarea id="pod-jira-filter" {...form.register("jira_filter_jql")} />
                </Field>
                <Field label="GitHub repo subset" htmlFor="pod-github-repos">
                  <Textarea id="pod-github-repos" {...form.register("github_repos")} />
                </Field>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" disabled={saveMutation.isPending}>
                Save
              </Button>
            </div>
          </form>
        </Dialog>

        <ConfirmDialog
          open={confirm.open}
          onOpenChange={(open) => !open && setConfirm({ open: false })}
          title={confirm.open ? confirm.title : ""}
          description={confirm.open ? confirm.description : ""}
          confirmLabel={confirm.open ? confirm.confirmLabel : "Confirm"}
          destructive={confirm.open ? confirm.destructive : false}
          onConfirm={() => {
            if (confirm.open) confirm.onConfirm();
            setConfirm({ open: false });
          }}
        />
      </div>
    </main>
  );

  function openCreate() {
    setEditing(null);
    form.reset(defaultNodeValues());
    setDialogOpen(true);
  }

  function openEdit(node: ConfigNodeResponse) {
    setEditing(node);
    form.reset({
      id: node.id,
      name: node.name,
      description: node.description ?? "",
      code: node.code ?? "",
      jira_project_key: node.jira_project_key ?? "",
      jira_base_jql: node.jira_base_jql ?? "",
      jira_board_id: node.jira_board_id ?? "",
      jira_filter_jql: node.jira_filter_jql ?? "",
      github_repos: (node.github_repos ?? []).join("\n"),
    });
    setDialogOpen(true);
  }

  function confirmDelete(kind: EntityKind, node: ConfigNodeResponse) {
    setConfirm({
      open: true,
      title: `Delete ${node.name}?`,
      description: `This removes ${node.id} from ${entityLabels[kind]}. Related links may also become invalid.`,
      confirmLabel: "Delete",
      destructive: true,
      onConfirm: () => deleteMutation.mutate({ kind, id: node.id }),
    });
  }
}

function IntegrationSummary({ node }: { node: ConfigNodeResponse }) {
  const items = [
    node.jira_project_key && `Jira ${node.jira_project_key}`,
    node.jira_base_jql && "Jira JQL",
    node.jira_board_id && `Board ${node.jira_board_id}`,
    node.jira_filter_jql && "Jira filter",
    ...(node.github_repos ?? []).map((repo) => `GitHub ${repo}`),
  ].filter((item): item is string => Boolean(item));

  if (items.length === 0) {
    return <span className="text-muted-foreground">-</span>;
  }
  return (
    <div className="flex max-w-80 flex-wrap gap-1">
      {items.map((item) => (
        <Badge key={item} tone="neutral">
          {item}
        </Badge>
      ))}
    </div>
  );
}

function RelationshipPanel({
  programs,
  projects,
  pods,
  members,
  onChanged,
  askConfirm,
}: {
  programs: ConfigNodeResponse[];
  projects: ConfigNodeResponse[];
  pods: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
  onChanged: () => Promise<void>;
  askConfirm: (state: ConfirmState) => void;
}) {
  const [projectId, setProjectId] = useState("");
  const [programId, setProgramId] = useState("");
  const [podId, setPodId] = useState("");
  const [linkProjectId, setLinkProjectId] = useState("");
  const [memberId, setMemberId] = useState("");
  const [memberRole, setMemberRole] = useState("developer");
  const [taskMemberId, setTaskMemberId] = useState("");
  const [taskId, setTaskId] = useState("");

  const run = async (action: () => Promise<unknown>, message: string) => {
    try {
      await action();
      await onChanged();
      toast.success(message);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  return (
    <DataPanel
      title="Links & Assignments"
      description="Maintain graph relationships without leaving empty or ambiguous mutations."
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <LinkForm
          title="Project to Program"
          canSubmit={Boolean(projectId && programId)}
          onSubmit={() =>
            void run(
              () => apiClient.linkProjectProgram(projectId, { program_id: programId }),
              "Linked project to program.",
            )
          }
          onUnlink={() =>
            confirmUnlink(
              askConfirm,
              "Unlink project from program?",
              () =>
                void run(
                  () => apiClient.unlinkProjectProgram(projectId, programId),
                  "Unlinked project from program.",
                ),
            )
          }
        >
          <NodeSelect
            value={projectId}
            onChange={setProjectId}
            items={projects}
            placeholder="Select project"
          />
          <NodeSelect
            value={programId}
            onChange={setProgramId}
            items={programs}
            placeholder="Select program"
          />
        </LinkForm>

        <LinkForm
          title="Pod to Project"
          canSubmit={Boolean(podId && linkProjectId)}
          onSubmit={() =>
            void run(() => apiClient.linkPodProject(podId, linkProjectId), "Linked pod to project.")
          }
          onUnlink={() =>
            confirmUnlink(
              askConfirm,
              "Unlink pod from project?",
              () =>
                void run(
                  () => apiClient.unlinkPodProject(podId, linkProjectId),
                  "Unlinked pod from project.",
                ),
            )
          }
        >
          <NodeSelect value={podId} onChange={setPodId} items={pods} placeholder="Select pod" />
          <NodeSelect
            value={linkProjectId}
            onChange={setLinkProjectId}
            items={projects}
            placeholder="Select project"
          />
        </LinkForm>

        <LinkForm
          title="Pod to Member"
          canSubmit={Boolean(podId && memberId && memberRole.trim())}
          onSubmit={() =>
            void run(
              () => apiClient.linkPodMember(podId, memberId, { role: memberRole }),
              "Linked member to pod.",
            )
          }
          onUnlink={() =>
            confirmUnlink(
              askConfirm,
              "Unlink member from pod?",
              () =>
                void run(
                  () => apiClient.unlinkPodMember(podId, memberId),
                  "Unlinked member from pod.",
                ),
            )
          }
        >
          <NodeSelect value={podId} onChange={setPodId} items={pods} placeholder="Select pod" />
          <NodeSelect
            value={memberId}
            onChange={setMemberId}
            items={members}
            placeholder="Select member"
          />
          <Input
            value={memberRole}
            onChange={(event) => setMemberRole(event.target.value)}
            placeholder="Role in pod"
          />
        </LinkForm>

        <LinkForm
          title="Member to Task"
          canSubmit={Boolean(taskMemberId && taskId.trim())}
          onSubmit={() =>
            void run(
              () => apiClient.assignMemberTask(taskMemberId, { task_id: taskId }),
              "Assigned task to member.",
            )
          }
          onUnlink={() =>
            confirmUnlink(
              askConfirm,
              "Unassign task from member?",
              () =>
                void run(
                  () => apiClient.unassignMemberTask(taskMemberId, taskId),
                  "Unassigned task from member.",
                ),
            )
          }
        >
          <NodeSelect
            value={taskMemberId}
            onChange={setTaskMemberId}
            items={members}
            placeholder="Select member"
          />
          <Input
            placeholder="Task ID"
            value={taskId}
            onChange={(event) => setTaskId(event.target.value)}
          />
        </LinkForm>
      </div>
    </DataPanel>
  );
}

function DirectoryPanel({ onChanged }: { onChanged: () => Promise<void> }) {
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directoryDebouncedQuery, setDirectoryDebouncedQuery] = useState("");
  const [directoryOffset, setDirectoryOffset] = useState(0);
  const [selectedDirectoryIds, setSelectedDirectoryIds] = useState<string[]>([]);
  const pageSize = 25;

  useEffect(() => {
    const timer = window.setTimeout(() => setDirectoryDebouncedQuery(directoryQuery), 300);
    return () => window.clearTimeout(timer);
  }, [directoryQuery]);

  useEffect(() => {
    setDirectoryOffset(0);
  }, [directoryDebouncedQuery]);

  const directorySearch = useQuery({
    queryKey: ["directory", directoryDebouncedQuery, directoryOffset],
    queryFn: () => apiClient.searchDirectory(directoryDebouncedQuery, pageSize, directoryOffset),
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

  return (
    <DataPanel
      title="Directory Import"
      description="Search synced users and promote selected people into configured members."
      action={
        <Button
          type="button"
          variant="outline"
          onClick={() => directorySyncMutation.mutate()}
          disabled={directorySyncMutation.isPending}
        >
          <DatabaseZap className="h-4 w-4" />
          Sync directory
        </Button>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput
            value={directoryQuery}
            onChange={setDirectoryQuery}
            placeholder="Search by name, email, or handle"
            className="min-w-72"
          />
          <Badge tone="neutral">
            {directorySearch.isFetching ? "Searching" : `${directoryTotal} matches`}
          </Badge>
          <Badge tone="info">{selectedDirectoryIds.length} selected</Badge>
        </div>

        {directorySearch.isError ? (
          <ErrorState
            error={directorySearch.error}
            onRetry={() => void directorySearch.refetch()}
          />
        ) : directoryItems.length === 0 ? (
          <EmptyState
            title={directorySearch.isFetching ? "Loading users" : "No directory matches"}
          />
        ) : (
          <div className="max-h-[32rem] overflow-y-auto rounded-md border border-border scrollbar-thin">
            <ul className="divide-y divide-border">
              {directoryItems.map((item) => (
                <li key={item.external_id} className="px-4 py-3">
                  <label className="flex cursor-pointer items-center gap-3">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-border accent-primary"
                      checked={selectedDirectoryIds.includes(item.external_id)}
                      onChange={() => toggleSelection(item.external_id)}
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
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setDirectoryOffset((current) => Math.max(0, current - pageSize))}
              disabled={directoryOffset === 0 || directorySearch.isFetching}
            >
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDirectoryOffset((current) => current + pageSize)}
              disabled={directoryOffset + pageSize >= directoryTotal || directorySearch.isFetching}
            >
              Next
            </Button>
          </div>
          <Button
            type="button"
            variant="primary"
            onClick={() => addDirectoryMembersMutation.mutate(selectedDirectoryIds)}
            disabled={selectedDirectoryIds.length === 0 || addDirectoryMembersMutation.isPending}
          >
            <UserPlus className="h-4 w-4" />
            Add selected
          </Button>
        </div>
      </div>
    </DataPanel>
  );

  function toggleSelection(id: string) {
    setSelectedDirectoryIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }
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

  return (
    <DataPanel
      title="Check-in Preferences"
      description="Set local check-in timing, weekdays, and reply windows per member."
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <Field label="Member" htmlFor="checkin-member">
            <NodeSelect
              id="checkin-member"
              value={memberId}
              onChange={(nextMember) => {
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
              items={members}
              placeholder="Select member"
            />
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
                  className={`min-h-8 rounded-md border px-3 text-xs font-medium ${
                    weekdays.includes(day.value)
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border text-muted-foreground hover:bg-surface-muted"
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
          <Button
            type="button"
            variant="primary"
            onClick={() => updateMutation.mutate()}
            disabled={!memberId || updateMutation.isPending}
          >
            Save preference
          </Button>
        </div>
        <div>
          <div className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
            Configured
          </div>
          {preferences.length === 0 ? (
            <EmptyState title="No preferences yet" />
          ) : (
            <div className="max-h-[32rem] divide-y divide-border overflow-y-auto rounded-md border border-border scrollbar-thin">
              {preferences.map((pref) => (
                <div key={pref.developer_id} className="px-3 py-2 text-sm">
                  <div className="font-medium">{pref.developer_id}</div>
                  <div className="text-xs text-muted-foreground">
                    {pref.local_time} / {pref.timezone ?? "UTC"} / {pref.weekdays.join(",")}
                  </div>
                  <Badge tone="info">{pref.reply_wait_seconds}s wait</Badge>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </DataPanel>
  );

  function toggleWeekday(day: number) {
    setWeekdays((current) =>
      current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort(),
    );
  }
}

function LinkForm({
  title,
  children,
  canSubmit,
  onSubmit,
  onUnlink,
}: {
  title: string;
  children: ReactNode;
  canSubmit: boolean;
  onSubmit: () => void;
  onUnlink: () => void;
}) {
  return (
    <section className="space-y-3 rounded-lg border border-border bg-surface-muted/40 p-3">
      <div className="text-sm font-semibold">{title}</div>
      <div className="space-y-2">{children}</div>
      <div className="flex gap-2">
        <Button type="button" variant="primary" onClick={onSubmit} disabled={!canSubmit}>
          Link
        </Button>
        <Button type="button" variant="outline" onClick={onUnlink} disabled={!canSubmit}>
          Unlink
        </Button>
      </div>
    </section>
  );
}

function NodeSelect({
  items,
  value,
  onChange,
  placeholder,
  id,
}: {
  items: ConfigNodeResponse[];
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  id?: string;
}) {
  return (
    <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {items.map((item) => (
        <option key={item.id} value={item.id}>
          {item.name}
        </option>
      ))}
    </Select>
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
      <img src={user.avatar_url} alt="" className="h-10 w-10 shrink-0 rounded-full object-cover" />
    );
  }
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
      {initials || "?"}
    </div>
  );
}

function saveNode(kind: EntityKind, values: NodeFormValues, editing: ConfigNodeResponse | null) {
  const payload = {
    id: values.id,
    name: values.name,
    description: blankToNull(values.description),
    code: blankToNull(values.code),
    ...(kind === "projects"
      ? {
          jira_project_key: blankToNull(values.jira_project_key),
          jira_base_jql: blankToNull(values.jira_base_jql),
          jira_board_id: blankToNull(values.jira_board_id),
          github_repos: repoList(values.github_repos),
        }
      : {}),
    ...(kind === "pods"
      ? {
          jira_filter_jql: blankToNull(values.jira_filter_jql),
          github_repos: repoList(values.github_repos),
        }
      : {}),
  };
  if (editing) {
    if (kind === "programs") return apiClient.updateConfigProgram(editing.id, payload);
    if (kind === "projects") return apiClient.updateConfigProject(editing.id, payload);
    if (kind === "pods") return apiClient.updateConfigPod(editing.id, payload);
    return apiClient.updateConfigMember(editing.id, payload);
  }
  if (kind === "programs") return apiClient.createConfigProgram(payload);
  if (kind === "projects") return apiClient.createConfigProject(payload);
  if (kind === "pods") return apiClient.createConfigPod(payload);
  return apiClient.createConfigMember(payload);
}

function deleteNode(kind: EntityKind, id: string) {
  if (kind === "programs") return apiClient.deleteConfigProgram(id);
  if (kind === "projects") return apiClient.deleteConfigProject(id);
  if (kind === "pods") return apiClient.deleteConfigPod(id);
  return apiClient.deleteConfigMember(id);
}

function filterNodes(nodes: ConfigNodeResponse[], query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return nodes;
  return nodes.filter((node) =>
    [
      node.id,
      node.name,
      node.description ?? "",
      node.code ?? "",
      node.jira_project_key ?? "",
      node.jira_base_jql ?? "",
      node.jira_board_id ?? "",
      node.jira_filter_jql ?? "",
      (node.github_repos ?? []).join(" "),
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalized),
  );
}

function defaultNodeValues(): NodeFormValues {
  return {
    id: "",
    name: "",
    description: "",
    code: "",
    jira_project_key: "",
    jira_base_jql: "",
    jira_board_id: "",
    jira_filter_jql: "",
    github_repos: "",
  };
}

function blankToNull(value: string | undefined): string | null {
  const normalized = value?.trim() ?? "";
  return normalized ? normalized : null;
}

function repoList(value: string | undefined): string[] {
  const seen = new Set<string>();
  const repos: string[] = [];
  for (const item of (value ?? "").split(/[,\n]/)) {
    const repo = item.trim();
    if (!repo || seen.has(repo)) continue;
    seen.add(repo);
    repos.push(repo);
  }
  return repos;
}

function confirmUnlink(
  askConfirm: (state: ConfirmState) => void,
  title: string,
  onConfirm: () => void,
) {
  askConfirm({
    open: true,
    title,
    description:
      "This removes an existing graph relationship. The underlying nodes remain configured.",
    confirmLabel: "Unlink",
    destructive: true,
    onConfirm,
  });
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "Operation failed.";
}
