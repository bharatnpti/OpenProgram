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

export function ProjectsPage() {
  const projects = useQuery({
    queryKey: ["directory", "projects"],
    queryFn: () => apiClient.projects(),
  });

  return (
    <main className="px-5 py-5">
      <header className="mb-4 border-b border-border pb-4">
        <h1 className="text-xl font-semibold">Projects</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Configured projects with latest rollup status.
        </p>
      </header>
      {projects.isLoading && <p className="text-sm text-muted-foreground">Loading projects...</p>}
      {projects.isError && <p className="text-sm text-red-600">Failed to load projects.</p>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {projects.data?.map((project) => (
          <Link
            key={project.id}
            to={`/projects/${project.id}`}
            className="rounded border border-border bg-white px-4 py-3 transition hover:border-primary/40"
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-medium">{project.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {project.code ?? project.id}
                </div>
              </div>
              {project.rag && <Badge tone={toneForRag(project.rag)}>{project.rag}</Badge>}
            </div>
            {project.description && (
              <p className="mt-2 text-sm text-muted-foreground">{project.description}</p>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
              <span>{project.pod_ids.length} pods</span>
              <span>{project.program_ids.length} programs</span>
            </div>
          </Link>
        ))}
      </div>
      {projects.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No projects configured yet. Use Admin Config to create projects and links.
        </p>
      )}
    </main>
  );
}
