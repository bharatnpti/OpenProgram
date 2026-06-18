import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { Rag } from "../api/schema";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";

const todayIso = () => new Date().toISOString().slice(0, 10);

function toneForRag(rag: Rag | null): "neutral" | "success" | "warning" | "danger" {
  if (rag === "green") return "success";
  if (rag === "amber") return "warning";
  if (rag === "red") return "danger";
  return "neutral";
}

export function PodDetailPage() {
  const { podId = "" } = useParams();
  const [asOf, setAsOf] = useState(todayIso);
  const pods = useQuery({ queryKey: ["directory", "pods", asOf], queryFn: () => apiClient.pods(asOf) });
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const blockers = useQuery({
    queryKey: ["persona", "blockers", podId, asOf],
    queryFn: () => apiClient.podBlockers(podId, asOf),
    enabled: Boolean(podId),
  });
  const checkins = useQuery({
    queryKey: ["persona", "checkins", podId, asOf],
    queryFn: () => apiClient.podCheckins(podId, asOf),
    enabled: Boolean(podId),
  });

  const pod = pods.data?.find((item) => item.id === podId);
  const relatedProjects = useMemo(
    () => projects.data?.filter((project) => pod?.project_ids.includes(project.id)) ?? [],
    [projects.data, pod?.project_ids],
  );

  return (
    <main className="px-5 py-5">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <Link to="/pods" className="text-xs text-muted-foreground hover:text-foreground">
            Back to pods
          </Link>
          <h1 className="mt-1 text-xl font-semibold">{pod?.name ?? podId}</h1>
          {pod?.description && (
            <p className="mt-1 text-sm text-muted-foreground">{pod.description}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            className="h-9 rounded border border-border bg-white px-3 text-sm"
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
          <Button
            onClick={() => {
              void pods.refetch();
              void projects.refetch();
              void blockers.refetch();
              void checkins.refetch();
            }}
          >
            Refresh
          </Button>
        </div>
      </header>

      <section className="mb-4 grid gap-3 md:grid-cols-3">
        <Stat label="Members" value={pod?.member_ids.length ?? 0} />
        <Stat label="Projects" value={pod?.project_ids.length ?? 0} />
        <Stat label="Blockers" value={blockers.data?.blockers.length ?? 0} />
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Related projects">
          {relatedProjects.length === 0 && (
            <p className="text-sm text-muted-foreground">No linked projects.</p>
          )}
          <div className="divide-y divide-border">
            {relatedProjects.map((project) => (
              <Link
                key={project.id}
                to={`/projects/${project.id}`}
                className="flex items-center justify-between gap-3 py-2 text-sm hover:text-primary"
              >
                <span>{project.name}</span>
                {project.rag && <Badge tone={toneForRag(project.rag)}>{project.rag}</Badge>}
              </Link>
            ))}
          </div>
        </Panel>

        <Panel title="Check-ins">
          {checkins.isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}
          {checkins.data && (
            <div className="space-y-2 text-sm">
              <div className="grid grid-cols-3 gap-2 text-center">
                <Stat label="Confirmed" value={checkins.data.confirmed} compact />
                <Stat label="Stale" value={checkins.data.stale} compact />
                <Stat label="Missing" value={checkins.data.missing} compact />
              </div>
              <div className="divide-y divide-border">
                {checkins.data.developers.map((developer) => (
                  <div
                    key={developer.developer_id}
                    className="flex items-center justify-between gap-2 py-2"
                  >
                    <div>
                      <div className="font-medium">{developer.developer_name}</div>
                      <div className="text-xs text-muted-foreground">{developer.summary}</div>
                    </div>
                    <Badge tone={developer.state === "confirmed" ? "success" : "warning"}>
                      {developer.state}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Open blockers" className="xl:col-span-2">
          {blockers.isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}
          {blockers.data?.blockers.length === 0 && (
            <p className="text-sm text-muted-foreground">No blockers reported.</p>
          )}
          <div className="divide-y divide-border">
            {blockers.data?.blockers.map((blocker) => (
              <div key={blocker.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{blocker.description}</div>
                  <div className="text-xs text-muted-foreground">
                    {blocker.owner_name} / {blocker.source}
                  </div>
                </div>
                <Badge tone="warning">{blocker.age_days}d</Badge>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </main>
  );
}

function Panel({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded border border-border bg-white ${className ?? ""}`}>
      <div className="border-b border-border px-4 py-3 text-sm font-semibold">{title}</div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function Stat({
  label,
  value,
  compact,
}: {
  label: string;
  value: number;
  compact?: boolean;
}) {
  return (
    <div className={`rounded border border-border bg-white ${compact ? "px-2 py-2" : "px-4 py-3"}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`font-semibold ${compact ? "text-base" : "text-2xl"}`}>{value}</div>
    </div>
  );
}
