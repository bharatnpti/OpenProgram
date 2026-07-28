import * as TabsPrimitive from "@radix-ui/react-tabs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseZap, Link2, ShieldAlert, Settings2, UserPlus } from "lucide-react";
import type { ReactNode } from "react";

import { apiClient } from "../api/client";
import type { ConfigNodeResponse } from "../api/schema";
import { CheckinPreferencesPanel } from "../features/admin/CheckinPreferencesPanel";
import { DirectoryPanel } from "../features/admin/DirectoryPanel";
import { EntitiesPanel } from "../features/admin/EntitiesPanel";
import { RelationshipsPanel } from "../features/admin/RelationshipsPanel";
import type { EntityKind } from "../features/admin/adminTypes";
import { cn } from "../lib/utils";

const TABS: { value: string; label: string; icon: typeof Settings2 }[] = [
  { value: "entities", label: "Entities", icon: Settings2 },
  { value: "relationships", label: "Links", icon: Link2 },
  { value: "directory", label: "Directory", icon: UserPlus },
  { value: "preferences", label: "Check-ins", icon: DatabaseZap },
];

export function AdminPage() {
  const queryClient = useQueryClient();

  const programs = useQuery({
    queryKey: ["config", "programs"],
    queryFn: apiClient.configPrograms,
  });
  const projects = useQuery({
    queryKey: ["config", "projects"],
    queryFn: apiClient.configProjects,
  });
  const workstreams = useQuery({
    queryKey: ["config", "workstreams"],
    queryFn: apiClient.configWorkstreams,
  });
  const pods = useQuery({ queryKey: ["config", "pods"], queryFn: apiClient.configPods });
  const members = useQuery({ queryKey: ["config", "members"], queryFn: apiClient.configMembers });
  const checkinPreferences = useQuery({
    queryKey: ["config", "checkin-preferences"],
    queryFn: apiClient.configCheckinPreferences,
  });
  const unmapped = useQuery({
    queryKey: ["config", "unmapped-members"],
    queryFn: apiClient.configUnmappedMembers,
  });
  const unmappedCount = unmapped.data?.length ?? 0;

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["config"] }),
      queryClient.invalidateQueries({ queryKey: ["directory"] }),
    ]);
  };

  const entities: Record<EntityKind, ConfigNodeResponse[]> = {
    programs: programs.data ?? [],
    projects: projects.data ?? [],
    workstreams: workstreams.data ?? [],
    pods: pods.data ?? [],
    members: members.data ?? [],
  };
  const entityQueries = { programs, projects, workstreams, pods, members };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-[28px] font-extrabold">Configuration</h1>
        <p className="mt-1 max-w-[640px] text-[15px] text-grey-secondary">
          Manage hierarchy, directory onboarding, graph links, assignments, and check-in timing.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Programs" value={programs.data?.length ?? "—"} />
        <Stat label="Projects" value={projects.data?.length ?? "—"} />
        <Stat label="Workstreams" value={workstreams.data?.length ?? "—"} />
        <Stat label="Pods" value={pods.data?.length ?? "—"} />
        <Stat
          label="Members"
          value={members.data?.length ?? "—"}
          tone={unmappedCount > 0 ? "text-rag-amber" : undefined}
          detail={
            unmappedCount > 0
              ? `${unmappedCount} unmapped (no chat ID)`
              : members.data
                ? "all mapped"
                : undefined
          }
          icon={
            unmappedCount > 0 ? <ShieldAlert size={14} className="text-rag-amber" /> : undefined
          }
        />
      </div>

      <TabsPrimitive.Root defaultValue="entities" className="flex flex-col gap-5">
        <TabsPrimitive.List className="flex flex-wrap gap-2">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <TabsPrimitive.Trigger
                key={tab.value}
                value={tab.value}
                className={cn(
                  "flex h-10 items-center gap-2 rounded-full px-4 text-[14px] font-bold transition-colors",
                  "data-[state=inactive]:border data-[state=inactive]:border-grey-border data-[state=inactive]:bg-white data-[state=inactive]:text-ink data-[state=inactive]:hover:bg-grey-fill",
                  "data-[state=active]:bg-magenta data-[state=active]:text-white",
                )}
              >
                <Icon size={16} />
                {tab.label}
              </TabsPrimitive.Trigger>
            );
          })}
        </TabsPrimitive.List>

        <TabsPrimitive.Content value="entities">
          <EntitiesPanel
            entities={entities}
            entityQueries={entityQueries}
            onChanged={invalidateAll}
          />
        </TabsPrimitive.Content>

        <TabsPrimitive.Content value="relationships">
          <RelationshipsPanel
            programs={programs.data ?? []}
            projects={projects.data ?? []}
            workstreams={workstreams.data ?? []}
            pods={pods.data ?? []}
            members={members.data ?? []}
            onChanged={invalidateAll}
          />
        </TabsPrimitive.Content>

        <TabsPrimitive.Content value="directory">
          <DirectoryPanel onChanged={invalidateAll} />
        </TabsPrimitive.Content>

        <TabsPrimitive.Content value="preferences">
          <CheckinPreferencesPanel
            members={members.data ?? []}
            preferences={checkinPreferences.data ?? []}
            onChanged={invalidateAll}
          />
        </TabsPrimitive.Content>
      </TabsPrimitive.Root>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  detail,
  icon,
}: {
  label: string;
  value: number | string;
  tone?: string;
  detail?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-3xl border border-grey-border bg-white p-4">
      <div className={cn("tabular-nums text-[24px] font-extrabold", tone)}>{value}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-grey-secondary">
        {icon}
        {label}
      </div>
      {detail ? <div className="mt-0.5 text-[12px] text-grey-secondary">{detail}</div> : null}
    </div>
  );
}
