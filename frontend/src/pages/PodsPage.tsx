import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../api/client";
import type { Rag } from "../api/schema";
import { Badge } from "../components/ui/badge";

function toneForRag(rag: Rag | null): "neutral" | "success" | "warning" | "danger" {
  if (rag === "green") return "success";
  if (rag === "amber") return "warning";
  if (rag === "red") return "danger";
  return "neutral";
}

export function PodsPage() {
  const pods = useQuery({ queryKey: ["directory", "pods"], queryFn: () => apiClient.pods() });

  return (
    <main className="px-5 py-5">
      <header className="mb-4 border-b border-border pb-4">
        <h1 className="text-xl font-semibold">Pods</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Configured pods from the runtime directory.
        </p>
      </header>
      {pods.isLoading && <p className="text-sm text-muted-foreground">Loading pods...</p>}
      {pods.isError && <p className="text-sm text-red-600">Failed to load pods.</p>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {pods.data?.map((pod) => (
          <Link
            key={pod.id}
            to={`/pods/${pod.id}`}
            className="rounded border border-border bg-white px-4 py-3 transition hover:border-primary/40"
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-medium">{pod.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">{pod.id}</div>
              </div>
              {pod.rag && <Badge tone={toneForRag(pod.rag)}>{pod.rag}</Badge>}
            </div>
            {pod.description && (
              <p className="mt-2 text-sm text-muted-foreground">{pod.description}</p>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
              <span>{pod.member_ids.length} members</span>
              <span>{pod.project_ids.length} projects</span>
            </div>
          </Link>
        ))}
      </div>
      {pods.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No pods configured yet. Use Admin Config to create pods and links.
        </p>
      )}
    </main>
  );
}
