import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { Rag } from "../api/schema";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";

const todayIso = () => new Date().toISOString().slice(0, 10);

function toneForRag(rag: Rag): "neutral" | "success" | "warning" | "danger" {
  if (rag === "green") return "success";
  if (rag === "amber") return "warning";
  if (rag === "red") return "danger";
  return "neutral";
}

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [asOf, setAsOf] = useState(todayIso);
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });
  const progress = useQuery({
    queryKey: ["persona", "progress", projectId, asOf],
    queryFn: () => apiClient.projectProgress(projectId, asOf),
    enabled: Boolean(projectId),
  });

  const project = projects.data?.find((item) => item.id === projectId);
  const relatedPods = useMemo(
    () => pods.data?.filter((pod) => project?.pod_ids.includes(pod.id)) ?? [],
    [pods.data, project?.pod_ids],
  );

  return (
    <main className="px-5 py-5">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <Link to="/projects" className="text-xs text-muted-foreground hover:text-foreground">
            Back to projects
          </Link>
          <h1 className="mt-1 text-xl font-semibold">{project?.name ?? projectId}</h1>
          {project?.description && (
            <p className="mt-1 text-sm text-muted-foreground">{project.description}</p>
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
              void projects.refetch();
              void pods.refetch();
              void progress.refetch();
            }}
          >
            Refresh
          </Button>
        </div>
      </header>

      <section className="mb-4 grid gap-3 md:grid-cols-4">
        <Stat
          label="Progress"
          value={
            progress.data ? `${Math.round(progress.data.percent_complete)}%` : progress.isLoading ? "…" : "-"
          }
        />
        <Stat label="Done" value={progress.data?.green_tasks ?? 0} />
        <Stat label="At risk" value={progress.data?.amber_tasks ?? 0} />
        <Stat label="Blocked" value={progress.data?.red_tasks ?? 0} />
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Associated pods">
          {relatedPods.length === 0 && (
            <p className="text-sm text-muted-foreground">No linked pods.</p>
          )}
          <div className="divide-y divide-border">
            {relatedPods.map((pod) => (
              <Link
                key={pod.id}
                to={`/pods/${pod.id}`}
                className="flex items-center justify-between gap-3 py-2 text-sm hover:text-primary"
              >
                <span>{pod.name}</span>
                {pod.rag && <Badge tone={toneForRag(pod.rag)}>{pod.rag}</Badge>}
              </Link>
            ))}
          </div>
        </Panel>

        <Panel title="Rollup">
          {progress.data ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">RAG</span>
                <Badge tone={toneForRag(progress.data.rag)}>{progress.data.rag}</Badge>
              </div>
              <div className="text-muted-foreground">Source: {progress.data.source}</div>
              <div className="text-muted-foreground">Total tasks: {progress.data.total_tasks}</div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Progress unavailable.</p>
          )}
        </Panel>

        <Panel title="Task breakdown" className="xl:col-span-2">
          {progress.isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}
          {progress.data?.tasks.length === 0 && (
            <p className="text-sm text-muted-foreground">No tasks assigned.</p>
          )}
          <div className="divide-y divide-border">
            {progress.data?.tasks.map((task) => (
              <div key={task.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{task.name}</div>
                  <div className="text-xs text-muted-foreground">{task.source}</div>
                </div>
                <Badge tone={toneForRag(task.rag)}>{task.rag}</Badge>
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

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded border border-border bg-white px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  );
}
