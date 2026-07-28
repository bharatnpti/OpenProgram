import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, NodeKind } from "../../api/schema";
import { cn } from "../../lib/utils";

const NAV_DESTINATIONS = [
  { label: "Today", hint: "Persona home", to: "/today" },
  { label: "Delivery", hint: "Graph explorer", to: "/delivery" },
  { label: "Signals", hint: "Risks, drift & flow", to: "/signals" },
  { label: "Coordination", hint: "Requests & briefs", to: "/coordination" },
];

type PaletteRow = {
  key: string;
  label: string;
  hint: string;
  dotClass: string;
  to: string;
};

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  const programs = useQuery({
    queryKey: ["directory", "programs"],
    queryFn: () => apiClient.programs(),
    enabled: open,
  });
  const projects = useQuery({
    queryKey: ["directory", "projects"],
    queryFn: () => apiClient.projects(),
    enabled: open,
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams"],
    queryFn: () => apiClient.workstreams(),
    enabled: open,
  });
  const pods = useQuery({
    queryKey: ["directory", "pods"],
    queryFn: () => apiClient.pods(),
    enabled: open,
  });

  const rows = useMemo<PaletteRow[]>(() => {
    const screenRows: PaletteRow[] = NAV_DESTINATIONS.map((item) => ({
      key: `nav-${item.to}`,
      label: item.label,
      hint: item.hint,
      dotClass: "bg-magenta rounded-sm",
      to: item.to,
    }));
    const entityRows = ([] as PaletteRow[]).concat(
      toRows(programs.data, "program", "bg-magenta"),
      toRows(projects.data, "project", "bg-rag-info"),
      toRows(workstreams.data, "workstream", "bg-rag-amber"),
      toRows(pods.data, "pod", "bg-rag-green"),
    );
    const all = [...screenRows, ...entityRows];
    const needle = query.trim().toLowerCase();
    const filtered = needle ? all.filter((row) => row.label.toLowerCase().includes(needle)) : all;
    return filtered.slice(0, 9);
  }, [query, programs.data, projects.data, workstreams.data, pods.data]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-center bg-black/40 pt-[12vh]"
      onClick={onClose}
    >
      <div
        className="h-fit w-[620px] max-w-[90vw] animate-op-pop rounded-3xl bg-white shadow-op-palette"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-grey-border px-6 py-4">
          <Search size={18} className="text-grey-secondary" />
          <input
            ref={inputRef}
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search screens, projects, workstreams, pods…"
            className="flex-1 border-none bg-transparent text-lg outline-none placeholder:text-grey-secondary"
          />
          <kbd className="rounded-md border border-grey-border bg-white px-2 py-0.5 text-xs text-grey-secondary">
            Esc
          </kbd>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-2">
          {rows.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-grey-secondary">No matches</div>
          ) : (
            rows.map((row) => (
              <button
                key={row.key}
                type="button"
                onClick={() => {
                  navigate(row.to);
                  onClose();
                  setQuery("");
                }}
                className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left hover:bg-grey-fill"
              >
                <span className={cn("inline-block h-2.5 w-2.5 shrink-0", row.dotClass)} />
                <span className="flex-1 font-bold">{row.label}</span>
                <span className="text-xs text-grey-secondary">{row.hint}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function toRows(
  items: DirectoryItemResponse[] | undefined,
  kind: NodeKind,
  dotClass: string,
): PaletteRow[] {
  if (!items) {
    return [];
  }
  return items.map((item) => ({
    key: `${kind}-${item.id}`,
    label: item.name,
    hint: kind,
    dotClass: cn(dotClass, "rounded-full"),
    to: `/delivery/${kind}/${encodeURIComponent(item.id)}`,
  }));
}
