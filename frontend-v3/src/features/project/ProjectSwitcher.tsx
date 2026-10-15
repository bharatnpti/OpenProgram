import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronRight, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useMemberId, useOwnTree, usePods, useWorkstreams } from "../../app/directory";
import { useRole } from "../../app/role";
import { cn } from "../../lib/utils";
import { ownProjects } from "../delivery/ownTree";
import { ALL_PROJECTS, type HubPlace, type HubTab } from "../hub/hubPaths";
import { hubHref, podChoices, verdictWords } from "../hub/hubTabs";
import {
  opens,
  rowHeight,
  treeRows,
  VERDICT_CHIPS,
  type TreeProject,
  type TreeRow,
  type VerdictChip,
} from "../hub/switcherTree";
import { usePortfolioIndex } from "../hub/useHub";
import { VerdictDot } from "../hub/VerdictDot";
import { visibleRange } from "../portfolio/windowing";
import { useProjectOptions } from "./useProjectOptions";

/** Past this many projects a search asks the server, which reads them all. */
export const SERVER_SEARCH_FROM = 100;
/** Past this many rows only the ones near the list's view are drawn. */
const WINDOW_FROM = 100;

/**
 * The hub's title, which opens the switcher: the navigator tree. All projects
 * first (for the readers of project lists), then Yours and Recent, then each
 * programme with its projects, worst first, and under each project its pods.
 * Every row shows its date verdict as a dot; chips keep Off track, At risk or
 * No date; a search covers names, programmes, Jira keys and pods, and asks the
 * server once a tenant has more than a hundred projects. A pick keeps the tab
 * and the viewing day, and keeps the pod when it works on the project picked.
 * The arrow keys move, → and ← open and fold, Enter opens, Escape closes. On
 * a phone the list is a full-screen sheet.
 */
export function ProjectSwitcher({
  place,
  tab,
  title,
}: {
  place: HubPlace;
  tab: HubTab;
  /** What the title says: All projects, the project's name, or the pod's. */
  title: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <h1 className="max-w-full text-[30px] font-extrabold tracking-tight max-sm:text-[26px]">
        <Dialog.Trigger
          aria-label={`${title}. Switch project or pod`}
          className="group inline-flex max-w-full items-center gap-2 rounded-2xl text-left hover:bg-grey-fill"
        >
          <span className="min-w-0">{title}</span>
          <ChevronDown
            aria-hidden
            size={22}
            className="flex-none text-grey-secondary group-hover:text-ink"
          />
        </Dialog.Trigger>
      </h1>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-white shadow-op-palette sm:inset-auto sm:left-1/2 sm:top-[8vh] sm:w-[min(92vw,600px)] sm:-translate-x-1/2 sm:rounded-3xl animate-op-pop"
        >
          <Dialog.Title className="sr-only">Switch project or pod</Dialog.Title>
          <SwitcherBody place={place} tab={tab} onClose={() => setOpen(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** The projects as the tree lists them: each with its verdict, its pods and its workstreams. */
function useTreeProjects(): {
  projects: TreeProject[];
  programs: { id: string; name: string }[];
  recent: string[];
  total: number;
  loading: boolean;
} {
  const { access } = useRole();
  const choice = useProjectOptions();
  const index = usePortfolioIndex();
  const pods = usePods();
  const tree = useOwnTree();
  const workstreams = useWorkstreams();
  const memberId = useMemberId();
  const projects = useMemo<TreeProject[]>(() => {
    const rows = new Map((index.data?.projects ?? []).map((row) => [row.id, row]));
    const own = tree.data ? ownProjects(tree.data) : [];
    const ownPods = new Set(
      own.flatMap((p) => p.pods.filter((pod) => pod.own).map((pod) => pod.id)),
    );
    for (const pod of pods.data ?? []) {
      if (memberId && pod.member_ids.includes(memberId)) ownPods.add(pod.id);
    }
    return choice.options.map((option) => {
      const row = rows.get(option.id);
      const podList = access.hub.bulk
        ? podChoices(
            row?.pods ?? null,
            (pods.data ?? []).filter((pod) => pod.project_ids.includes(option.id)),
            ownPods,
            access.hub.countsOnly,
          )
        : podChoices(
            null,
            (own.find((project) => project.id === option.id)?.pods ?? []).filter((p) => p.opens),
            ownPods,
            false,
          );
      const inUse = access.hub.bulk
        ? (workstreams.data ?? [])
            .filter((item) => item.in_use !== false && item.project_ids.includes(option.id))
            .map((item) => item.name)
        : [];
      return { ...option, verdict: row?.verdict ?? null, pods: podList, workstreams: inUse };
    });
  }, [
    access.hub.bulk,
    access.hub.countsOnly,
    choice.options,
    index.data,
    memberId,
    pods.data,
    tree.data,
    workstreams.data,
  ]);
  return {
    projects,
    programs: choice.programs,
    recent: choice.recent,
    total: index.data?.total ?? projects.length,
    loading: choice.loading,
  };
}

function SwitcherBody({
  place,
  tab,
  onClose,
}: {
  place: HubPlace;
  tab: HubTab;
  onClose: () => void;
}) {
  const { access } = useRole();
  const navigate = useNavigate();
  const location = useLocation();
  const tree = useTreeProjects();
  const [query, setQuery] = useState("");
  const [typed, setTyped] = useState("");
  const [chips, setChips] = useState<VerdictChip[]>([]);
  const [toggledGroups, setToggledGroups] = useState<Set<string>>(() => new Set());
  const currentProject = place.level === "all" ? null : place.projectId;
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(currentProject ? [currentProject] : []),
  );
  const [active, setActive] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const withAll = access.hub.tabs.all.includes("date");

  // The search waits for typing to stop before it narrows (and before it asks the server).
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(typed), 150);
    return () => window.clearTimeout(timer);
  }, [typed]);

  // Past a hundred projects the server searches them all, by name, code, Jira key or programme.
  const serverSearch = tree.total > SERVER_SEARCH_FROM && query.trim() !== "";
  const found = useQuery({
    queryKey: ["portfolio", "delivery", "switcher", query.trim()],
    queryFn: () => apiClient.portfolioDelivery({ q: query.trim(), limit: 200 }),
    enabled: access.hub.bulk && serverSearch,
  });

  const rows = useMemo<TreeRow[]>(() => {
    const state = { query, chips, toggledGroups, expanded, withAll };
    if (!serverSearch || !found.data) {
      return treeRows(tree.projects, tree.recent, tree.programs, state);
    }
    const ids = new Set(found.data.projects.map((project) => project.id));
    const words = query.trim().toLowerCase();
    const kept = tree.projects.filter(
      (project) =>
        ids.has(project.id) || project.pods.some((pod) => pod.name.toLowerCase().includes(words)),
    );
    return treeRows(kept, tree.recent, tree.programs, { ...state, query: "" });
  }, [chips, expanded, found.data, query, serverSearch, toggledGroups, tree, withAll]);

  const stops = useMemo(
    () =>
      rows.map((row, index) => (opens(row) || isFoldable(row) ? index : -1)).filter((i) => i >= 0),
    [rows],
  );
  const index = stops.includes(active) ? active : (stops[0] ?? 0);

  // Only the rows near the list's view are drawn once it is long.
  const sizes = useMemo(() => rows.map(rowHeight), [rows]);
  const windowed = rows.length > WINDOW_FROM;
  const height = list.current?.clientHeight ?? 520;
  const range = windowed
    ? visibleRange(sizes, scrollTop, height, 300)
    : { first: 0, last: rows.length - 1, before: 0, after: 0 };

  useEffect(() => {
    const element = list.current;
    if (!element) return;
    const top = sizes.slice(0, index).reduce((sum, size) => sum + size, 0);
    const bottom = top + (sizes[index] ?? 0);
    if (top < element.scrollTop) element.scrollTop = top;
    else if (bottom > element.scrollTop + element.clientHeight) {
      element.scrollTop = bottom - element.clientHeight;
    }
  }, [index, sizes]);

  const from = { place, tab };
  const hrefOf = (row: TreeRow): string | null => {
    if (row.kind === "all")
      return hubHref(access.hub.tabs, from, { level: "all" }, tab, location.search);
    if (row.kind === "pod") {
      return hubHref(
        access.hub.tabs,
        from,
        { level: "pod", projectId: row.project.id, podId: row.pod.id },
        tab,
        location.search,
      );
    }
    if (row.kind === "project") {
      // The pod stays when it works on the project picked; otherwise the project itself.
      const keepPod =
        place.level === "pod" && row.project.pods.some((pod) => pod.id === place.podId);
      const next: HubPlace = keepPod
        ? {
            level: "pod",
            projectId: row.project.id,
            podId: place.level === "pod" ? place.podId : "",
          }
        : { level: "project", projectId: row.project.id };
      return hubHref(access.hub.tabs, from, next, tab, location.search);
    }
    return null;
  };

  const toggleGroup = (key: string) =>
    setToggledGroups((current) => {
      const next = new Set(current);
      const id = key.replace(/^heading:/, "");
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const setOpenPods = (projectId: string, on: boolean) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (on) next.add(projectId);
      else next.delete(projectId);
      return next;
    });

  const pick = (row: TreeRow | undefined) => {
    if (!row) return;
    if (row.kind === "heading") {
      if (row.foldable) toggleGroup(row.key);
      return;
    }
    const href = hrefOf(row);
    onClose();
    if (href && href !== `${location.pathname}${location.search}`) navigate(href);
  };

  const move = (step: 1 | -1) => {
    if (stops.length === 0) return;
    const at = stops.indexOf(index);
    const next = (at + step + stops.length) % stops.length;
    setActive(stops[next]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const row = rows[index];
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "ArrowRight" && row) {
      if (row.kind === "project" && !row.expanded && row.project.pods.length > 0) {
        event.preventDefault();
        setOpenPods(row.project.id, true);
      } else if (row.kind === "heading" && row.folded) {
        event.preventDefault();
        toggleGroup(row.key);
      }
    } else if (event.key === "ArrowLeft" && row) {
      if (row.kind === "project" && row.expanded) {
        event.preventDefault();
        setOpenPods(row.project.id, false);
      } else if (row.kind === "pod") {
        event.preventDefault();
        const parent = rows.findIndex(
          (other, at) =>
            at < index && other.kind === "project" && other.project.id === row.project.id,
        );
        if (parent >= 0) setActive(parent);
      } else if (row.kind === "heading" && row.foldable && !row.folded) {
        event.preventDefault();
        toggleGroup(row.key);
      }
    } else if (event.key === "Home" && event.ctrlKey) {
      event.preventDefault();
      setActive(stops[0] ?? 0);
    } else if (event.key === "End" && event.ctrlKey) {
      event.preventDefault();
      setActive(stops[stops.length - 1] ?? 0);
    } else if (event.key === "Enter") {
      event.preventDefault();
      pick(row);
    }
  };

  const projectCount = rows.filter((row) => row.kind === "project").length;
  const searching = query.trim() !== "" || chips.length > 0;
  const placeholder = access.hub.bulk
    ? "Find a project, programme, Jira key or pod…"
    : "Find a project or pod…";

  return (
    <>
      <div className="flex items-center gap-3 border-b border-grey-border px-5 py-4">
        <Search size={18} aria-hidden className="flex-none text-grey-secondary" />
        <input
          role="combobox"
          aria-expanded
          aria-haspopup="tree"
          aria-controls="hub-switcher-tree"
          aria-activedescendant={rows[index] ? `hub-row-${index}` : undefined}
          aria-autocomplete="list"
          aria-label="Find a project by name, programme or Jira key, or a pod by name"
          autoComplete="off"
          spellCheck={false}
          value={typed}
          onChange={(event) => {
            setTyped(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          className="min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-grey-secondary"
        />
        <Dialog.Close className="flex-none rounded-md border border-grey-border px-2 py-0.5 text-[11px] text-grey-secondary sm:hidden">
          Close
        </Dialog.Close>
        <kbd className="hidden flex-none rounded-md border border-grey-border px-2 py-0.5 text-[11px] text-grey-secondary sm:inline">
          Esc
        </kbd>
      </div>
      {access.hub.bulk ? (
        <div
          role="group"
          aria-label="Keep only"
          className="flex flex-wrap gap-2 border-b border-grey-border px-5 py-2.5"
        >
          {VERDICT_CHIPS.map((chip) => {
            const on = chips.includes(chip.value);
            return (
              <button
                key={chip.value}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  setChips((current) =>
                    on ? current.filter((value) => value !== chip.value) : [...current, chip.value],
                  );
                  setActive(0);
                }}
                className={cn(
                  "inline-flex min-h-9 items-center gap-2 rounded-full border px-3 text-[13px] font-bold",
                  on
                    ? "border-ink bg-ink text-white"
                    : "border-grey-border text-grey-body hover:bg-grey-fill",
                )}
              >
                <VerdictDot verdict={chip.value} />
                {chip.label}
              </button>
            );
          })}
        </div>
      ) : null}
      <ul
        ref={list}
        id="hub-switcher-tree"
        role="tree"
        aria-label="Projects and pods"
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        className="min-h-0 flex-1 overflow-y-auto p-2 sm:max-h-[min(62vh,560px)] sm:flex-none"
      >
        {tree.loading ? (
          <li className="px-4 py-8 text-center text-[14px] text-grey-secondary">Loading…</li>
        ) : projectCount === 0 && searching ? (
          <li className="px-4 py-8 text-center text-[14px] text-grey-secondary">
            {query.trim()
              ? `No project or pod matches “${query.trim()}”.`
              : "No project has that answer."}
          </li>
        ) : (
          <>
            {range.before > 0 ? <li role="presentation" style={{ height: range.before }} /> : null}
            {rows.slice(range.first, range.last + 1).map((row, offset) => {
              const at = range.first + offset;
              return (
                <TreeLine
                  key={row.key}
                  row={row}
                  at={at}
                  active={at === index}
                  place={place}
                  onHover={() => (opens(row) || isFoldable(row) ? setActive(at) : undefined)}
                  onPick={() => pick(row)}
                  onToggle={
                    row.kind === "project" && row.project.pods.length > 0
                      ? () => setOpenPods(row.project.id, !row.expanded)
                      : undefined
                  }
                />
              );
            })}
            {range.after > 0 ? <li role="presentation" style={{ height: range.after }} /> : null}
          </>
        )}
      </ul>
      <p className="flex flex-wrap gap-x-4 border-t border-grey-border px-5 py-2.5 text-[12px] text-grey-secondary">
        <span>↑ ↓ to move · → ← to open or fold · Enter to open · Esc to close</span>
        {serverSearch && found.isFetching ? <span>Searching every project…</span> : null}
      </p>
    </>
  );
}

function isFoldable(row: TreeRow): boolean {
  return row.kind === "heading" && row.foldable;
}

/** One row of the tree: All projects, a heading, a project or a pod. */
function TreeLine({
  row,
  at,
  active,
  place,
  onHover,
  onPick,
  onToggle,
}: {
  row: TreeRow;
  at: number;
  active: boolean;
  place: HubPlace;
  onHover: () => void;
  onPick: () => void;
  onToggle?: () => void;
}) {
  const { access } = useRole();
  const base = "flex cursor-pointer items-center gap-3 rounded-xl px-3";
  const look = active ? "bg-grey-fill" : "hover:bg-grey-fill";
  if (row.kind === "heading") {
    const label = (
      <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        {row.foldable ? (
          row.folded ? (
            <ChevronRight size={13} aria-hidden />
          ) : (
            <ChevronDown size={13} aria-hidden />
          )
        ) : null}
        {row.label}
        {row.folded ? (
          <span className="font-normal normal-case tracking-normal">· {row.count}</span>
        ) : null}
      </span>
    );
    return row.foldable ? (
      <li
        id={`hub-row-${at}`}
        role="treeitem"
        aria-level={1}
        aria-expanded={!row.folded}
        aria-selected={active}
        onMouseMove={onHover}
        onClick={onPick}
        className={cn(base, look, "h-[34px] min-h-0")}
      >
        {label}
      </li>
    ) : (
      <li role="presentation" className="flex h-[34px] items-end px-3 pb-1">
        {label}
      </li>
    );
  }
  if (row.kind === "all") {
    const current = place.level === "all";
    return (
      <li
        id={`hub-row-${at}`}
        role="treeitem"
        aria-level={1}
        aria-selected={active}
        aria-current={current ? "page" : undefined}
        onMouseMove={onHover}
        onClick={onPick}
        className={cn(base, look, "h-[46px]")}
      >
        <span aria-hidden className="h-2.5 w-2.5 flex-none rounded-sm bg-magenta" />
        <span className="min-w-0 flex-1 truncate text-[15px] font-extrabold">{ALL_PROJECTS}</span>
        {current ? <Check size={15} aria-hidden className="flex-none text-ink" /> : null}
      </li>
    );
  }
  if (row.kind === "project") {
    const project = row.project;
    const current = place.level === "project" && place.projectId === project.id;
    const hint = [project.jiraKey, row.programme].filter(Boolean).join(" · ");
    return (
      <li
        id={`hub-row-${at}`}
        role="treeitem"
        aria-level={2}
        aria-selected={active}
        aria-expanded={project.pods.length > 0 ? row.expanded : undefined}
        aria-current={current ? "page" : undefined}
        onMouseMove={onHover}
        onClick={onPick}
        className={cn(base, look, project.workstreams.length > 0 ? "h-[62px]" : "h-[46px]")}
      >
        {onToggle ? (
          <button
            type="button"
            tabIndex={-1}
            aria-label={`${row.expanded ? "Fold" : "Show"} ${project.name}'s pods`}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
            className="-ml-1 grid h-8 w-6 flex-none place-items-center rounded-md text-grey-secondary hover:text-ink"
          >
            {row.expanded ? (
              <ChevronDown size={15} aria-hidden />
            ) : (
              <ChevronRight size={15} aria-hidden />
            )}
          </button>
        ) : (
          <span aria-hidden className="w-5 flex-none" />
        )}
        <VerdictDot verdict={project.verdict} none={!access.hub.bulk} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-bold">
            {project.name}
            {access.hub.bulk ? (
              <span className="sr-only">, {verdictWords(project.verdict)}</span>
            ) : null}
          </span>
          {project.workstreams.length > 0 ? (
            <span className="block truncate text-[12px] text-grey-secondary">
              {project.workstreams.join(" · ")}
            </span>
          ) : null}
        </span>
        {hint ? (
          <span className="hidden min-w-0 max-w-[40%] truncate text-right text-[12px] text-grey-secondary sm:block">
            {hint}
          </span>
        ) : null}
        {current ? <Check size={15} aria-hidden className="flex-none text-ink" /> : null}
      </li>
    );
  }
  const current =
    place.level === "pod" && place.podId === row.pod.id && place.projectId === row.project.id;
  const dated = access.hub.bulk && (row.pod.verdict !== null || row.pod.total !== null);
  return (
    <li
      id={`hub-row-${at}`}
      role="treeitem"
      aria-level={3}
      aria-selected={active}
      aria-current={current ? "page" : undefined}
      onMouseMove={onHover}
      onClick={onPick}
      className={cn(base, look, "h-[42px] pl-12")}
    >
      <VerdictDot verdict={row.pod.verdict} none={!dated} />
      <span className="min-w-0 flex-1 truncate text-[14px]">
        {row.pod.name}
        {dated ? <span className="sr-only">, {verdictWords(row.pod.verdict)}</span> : null}
      </span>
      {row.pod.own ? (
        <span className="flex-none text-[12px] text-grey-secondary">Yours</span>
      ) : null}
      {current ? <Check size={15} aria-hidden className="flex-none text-ink" /> : null}
    </li>
  );
}
