import * as Dialog from "@radix-ui/react-dialog";
import { CornerDownLeft, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";

import { paletteTargets } from "../../app/access";
import { usePods, usePrograms, useProjects, useWorkstreams } from "../../app/directory";
import { shownNav } from "../../app/nav";
import { useRole } from "../../app/role";
import { cn } from "../../lib/utils";
import {
  directoryRows,
  matchRows,
  noPodNote,
  peopleRows,
  screenRows,
  type NamedPerson,
  type PaletteRow,
} from "./paletteRows";

const KIND_DOT: Record<PaletteRow["kind"], string> = {
  screen: "rounded-sm bg-magenta",
  program: "rounded-full bg-magenta",
  project: "rounded-full bg-rag-info",
  workstream: "rounded-full bg-rag-amber",
  pod: "rounded-full bg-rag-green",
  person: "rounded-full bg-ink",
};

/**
 * ⌘K / Ctrl+K: jump to a screen this role is offered, or to a program,
 * project, workstream (one that holds work), pod or named person, each where
 * this role has it (app/access.ts `paletteTargets`): a kind the role does not
 * read is not listed. Typing filters; the arrow keys move, Enter opens, Escape
 * closes, so it works by keyboard alone. A jump keeps the day being viewed.
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // Opened by a shortcut or a plain button, not a Radix trigger, so Radix has
  // nowhere to send focus back to: remember what had it, and return it there.
  const returnTo = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[10vh] z-50 w-[min(92vw,620px)] -translate-x-1/2 overflow-hidden rounded-3xl bg-white shadow-op-palette animate-op-pop"
          onOpenAutoFocus={() => {
            const focused = document.activeElement;
            returnTo.current = focused instanceof HTMLElement ? focused : null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnTo.current?.focus();
          }}
        >
          <Dialog.Title className="sr-only">Jump to</Dialog.Title>
          {/* Mounted only while open, so the directory is read only when it's wanted. */}
          <PaletteBody onClose={() => onOpenChange(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PaletteBody({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const roleState = useRole();
  const programs = usePrograms();
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const pods = usePods();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);

  const directory = useMemo(
    () => ({
      programs: programs.data ?? [],
      projects: projects.data ?? [],
      workstreams: workstreams.data ?? [],
      pods: pods.data ?? [],
    }),
    [programs.data, projects.data, workstreams.data, pods.data],
  );
  // Names only from the acting-as roster and the people the directory names:
  // an id nobody names is never shown as a person.
  const named = useMemo<NamedPerson[]>(() => {
    const fromDirectory = [...directory.pods, ...directory.projects, ...directory.workstreams]
      .flatMap((item) => item.people)
      .filter((person) => person.name)
      .map((person) => ({ id: person.member_id ?? person.id, name: person.name ?? "" }));
    return [...roleState.people, ...fromDirectory];
  }, [directory, roleState.people]);

  const targets = useMemo(() => paletteTargets(roleState.access, roleState), [roleState]);
  const rows = useMemo(
    () =>
      matchRows(
        [
          ...screenRows(shownNav(roleState.access)),
          ...directoryRows(directory, targets),
          ...peopleRows(named, directory.pods, targets.person),
        ],
        query,
      ),
    [directory, named, query, roleState.access, targets],
  );
  // A name that finds nobody may be a person in no pod: there is nowhere to take them.
  const noPod = useMemo(
    () => (rows.length === 0 && targets.person ? noPodNote(named, directory.pods, query) : null),
    [directory.pods, named, query, rows.length, targets.person],
  );
  const current = Math.min(active, Math.max(rows.length - 1, 0));
  const loading =
    programs.isLoading || projects.isLoading || workstreams.isLoading || pods.isLoading;
  const failed = programs.error ?? projects.error ?? pods.error ?? workstreams.error;

  useEffect(() => {
    list.current
      ?.querySelector<HTMLElement>(`[data-index="${current}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const go = (row: PaletteRow | undefined) => {
    if (!row) return;
    onClose();
    navigate(row.to);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const last = rows.length - 1;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(current >= last ? 0 : current + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(current <= 0 ? last : current - 1);
    } else if (event.key === "Home" && event.ctrlKey) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End" && event.ctrlKey) {
      event.preventDefault();
      setActive(last);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(rows[current]);
    }
  };

  return (
    <>
      <div className="flex items-center gap-3 border-b border-grey-border px-5 py-4">
        <Search size={18} aria-hidden className="flex-none text-grey-secondary" />
        <input
          role="combobox"
          aria-expanded
          aria-controls="palette-results"
          aria-activedescendant={rows.length > 0 ? `palette-option-${current}` : undefined}
          aria-autocomplete="list"
          aria-label="Jump to a screen, person, program, project, workstream or pod"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            // The list changes under the cursor, so the highlight goes back to the top.
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Jump to a screen, person, project or pod…"
          className="min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-grey-secondary"
        />
        <kbd className="flex-none rounded-md border border-grey-border px-2 py-0.5 text-[11px] text-grey-secondary">
          Esc
        </kbd>
      </div>
      <ul
        ref={list}
        id="palette-results"
        role="listbox"
        aria-label="Results"
        className="max-h-[min(60vh,520px)] overflow-y-auto p-2"
      >
        {rows.length === 0 ? (
          <li className="px-4 py-8 text-center text-[14px] text-grey-secondary">
            {loading ? (
              // The programs, projects and pods a name could match are not in yet.
              "Loading programs, projects and pods…"
            ) : (
              <>
                Nothing matches “{query.trim()}”.
                {noPod ? <span className="mt-1 block">{noPod}</span> : null}
              </>
            )}
          </li>
        ) : (
          rows.map((row, index) => (
            <li
              key={row.key}
              id={`palette-option-${index}`}
              data-index={index}
              role="option"
              aria-selected={index === current}
              onMouseMove={() => setActive(index)}
              onClick={() => go(row)}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-xl px-4 py-2.5",
                index === current ? "bg-grey-fill" : "hover:bg-grey-fill",
              )}
            >
              <span aria-hidden className={cn("h-2.5 w-2.5 flex-none", KIND_DOT[row.kind])} />
              <span className="min-w-0 flex-1 truncate text-[15px] font-bold">{row.label}</span>
              <span className="min-w-0 max-w-[45%] truncate text-right text-[12px] text-grey-secondary">
                {row.hint}
              </span>
              {index === current ? (
                <CornerDownLeft size={14} aria-hidden className="flex-none text-grey-secondary" />
              ) : null}
            </li>
          ))
        )}
      </ul>
      <p className="flex flex-wrap gap-x-4 border-t border-grey-border px-5 py-2.5 text-[12px] text-grey-secondary">
        <span>↑ ↓ to move · Enter to open · Esc to close</span>
        {loading && rows.length > 0 ? <span>Loading programs, projects and pods…</span> : null}
        {failed ? (
          <span className="text-rag-red">
            Only screens for now: {(failed as Error).message || "the directory did not load."}
          </span>
        ) : null}
      </p>
    </>
  );
}
