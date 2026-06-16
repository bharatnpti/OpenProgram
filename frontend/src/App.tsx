import { useQuery } from "@tanstack/react-query";
import { Activity, GitBranch, RefreshCw, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { apiClient } from "./api/client";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";

export function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: apiClient.health,
  });
  const ready = useQuery({
    queryKey: ["ready"],
    queryFn: apiClient.ready,
  });
  const graph = useQuery({
    queryKey: ["program-tree", "program-platform"],
    queryFn: () => apiClient.programTree("program-platform"),
  });

  const isRefreshing = health.isFetching || ready.isFetching || graph.isFetching;

  return (
    <main className="min-h-screen">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-5 py-6">
        <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">PulseOps</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Foundation shell for graph, integrations, workflows, and agents.
            </p>
          </div>
          <Button
            onClick={() => {
              void health.refetch();
              void ready.refetch();
              void graph.refetch();
            }}
            disabled={isRefreshing}
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </header>

        <section className="grid gap-3 md:grid-cols-3">
          <StatusPanel
            icon={<Activity className="h-4 w-4" />}
            label="API"
            value={health.data?.status ?? (health.isError ? "error" : "loading")}
            detail={health.data ? `${health.data.environment} / ${health.data.tenant_id}` : ""}
            tone={health.data?.status === "ok" ? "success" : "warning"}
          />
          <StatusPanel
            icon={<ShieldCheck className="h-4 w-4" />}
            label="Readiness"
            value={ready.data?.status ?? (ready.isError ? "error" : "loading")}
            detail={
              ready.data
                ? Object.entries(ready.data.dependencies)
                    .map(([name, ok]) => `${name}:${ok ? "ok" : "down"}`)
                    .join(" ")
                : ""
            }
            tone={ready.data?.status === "ok" ? "success" : "warning"}
          />
          <StatusPanel
            icon={<GitBranch className="h-4 w-4" />}
            label="Graph"
            value={graph.data?.root.name ?? (graph.isError ? "error" : "loading")}
            detail={
              graph.data
                ? `${graph.data.nodes.length} nodes / ${graph.data.edges.length} edges`
                : ""
            }
            tone={graph.data ? "success" : "warning"}
          />
        </section>

        <section className="grid gap-5 lg:grid-cols-[1fr_340px]">
          <div className="rounded border border-border bg-white">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Demo Graph</h2>
              <Badge tone={graph.data ? "success" : "neutral"}>program-platform</Badge>
            </div>
            <div className="divide-y divide-border">
              {graph.data?.nodes.map((node) => (
                <div key={node.id} className="grid grid-cols-[110px_1fr] gap-3 px-4 py-3 text-sm">
                  <Badge tone={node.kind === "task" ? "warning" : "neutral"}>{node.kind}</Badge>
                  <div>
                    <div className="font-medium">{node.name}</div>
                    <div className="text-xs text-muted-foreground">{node.id}</div>
                  </div>
                </div>
              ))}
              {!graph.data && (
                <div className="px-4 py-8 text-sm text-muted-foreground">
                  {graph.isError ? "Graph request failed." : "Loading graph..."}
                </div>
              )}
            </div>
          </div>

          <aside className="rounded border border-border bg-white">
            <div className="border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Phase 0 Surface</h2>
            </div>
            <div className="space-y-3 px-4 py-4 text-sm">
              <Row label="Backend" value="FastAPI + ports" />
              <Row label="Graph" value="time-bounded" />
              <Row label="Chat seam" value="Slack first" />
              <Row label="Workflow" value="Temporal heartbeat" />
              <Row label="Agent" value="LLM provider seam" />
            </div>
          </aside>
        </section>
      </div>
    </main>
  );
}

function StatusPanel({
  icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: "success" | "warning";
}) {
  return (
    <div className="rounded border border-border bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {icon}
          {label}
        </div>
        <Badge tone={tone}>{value}</Badge>
      </div>
      <div className="mt-3 min-h-5 text-sm text-foreground">{detail}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
