import * as Dialog from "@radix-ui/react-dialog";
import { CornerDownLeft, Search, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";

import { paletteTargets } from "../../app/access";
import { useOwnTree, usePods, usePrograms, useProjects, useWorkstreams } from "../../app/directory";
import { shownNav } from "../../app/nav";
import { useRole } from "../../app/role";
import { useAssistantControl } from "../../features/assistant/assistantContext";
import { ASK_LABEL } from "../../features/assistant/persona";
import { fullPodIds } from "../../features/delivery/ownTree";
import { cn } from "../../lib/utils";
import { RagDot } from "../ui/Bits";
import {
  askRow,
  directoryRows,
  matchRows,
  noPodNote,
  ownRows,
  paletteMark,
  peopleRows,
  screenRows,
  type NamedPerson,
  type PaletteRow,
} from "./paletteRows";

/**
 * A node's status dot, drawn as Delivery's navigator draws it, its words on
 * hover (the row says them to a screen reader after the name); a screen, the
 * assistant and a person get a mark in no status colour, since they have none.
 */
function RowMark({ row }: { row: PaletteRow }) {
  const mark = paletteMark(row);
  if (mark.kind === "status") {
    return (
      <span title={mark.words} className="inline-flex flex-none">
        <RagDot rag={row.rag} />
      </span>
    );
  }
  if (mark.kind === "person") {
    return <UserRound size={12} strokeWidth={2.5} aria-hidden className="flex-none text-ink" />;
  }
  if (mark.kind === "plain") {
    // A node whose colour the role does not read: no dot, as in Delivery's navigator.
    return <span aria-hidden className="h-2.5 w-2.5 flex-none" />;
  }
  return (
    <span
      aria-hidden
      className={cn(
        "h-2.5 w-2.5 flex-none",
        mark.kind === "screen" ? "rounded-sm bg-magenta" : "rounded-full border-2 border-ink",
      )}
    />
  );
}

/** A node's status in words, for a screen reader: "Payments Pod, At risk". */
function StatusWords({ row }: { row: PaletteRow }) {
  const mark = paletteMark(row);
  return mark.kind === "status" ? <span className="sr-only">, {mark.words}</span> : null;
}

/**
 * ⌘K / Ctrl+K: jump to a screen this role is offered, or to a program,
 * project, workstream (one that holds work), pod or named person, each where
 * this role has it (app/access.ts `paletteTargets`): a kind the role does not
 * read is not listed. A program, project, workstream or pod carries its status
 * as Delivery's navigator does (`paletteMark`). For a role with the assistant,
 * its row (ASK_LABEL) opens it, and a query becomes a question in it, last in
 * the list. Typing filters;
 * the arrow keys move, Enter opens, Escape closes, so it works by keyboard
 * alone. A jump keeps the day being viewed.
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

  const assistant = useAssistantControl();
  const asks = roleState.access.assistant && assistant !== null;
  const targets = useMemo(() => paletteTargets(roleState.access), [roleState.access]);
  // Under the `own` scope the palette lists what that person's Delivery lists, and
  // people only in the pods whose whole panel (their check-ins) is theirs to open.
  const own = roleState.access.deliveryScope === "own";
  const tree = useOwnTree(own);
  const rows = useMemo(() => {
    const ask = askRow(query, ASK_LABEL);
    const nodes = own
      ? tree.data
        ? ownRows(tree.data, targets)
        : []
      : directoryRows(directory, targets);
    const full = own && tree.data ? fullPodIds(tree.data) : null;
    const peoplePods = full ? directory.pods.filter((pod) => full.has(pod.id)) : directory.pods;
    // With no query, the assistant's row sits after the screens; a query's question comes last.
    const found = matchRows(
      [
        ...screenRows(shownNav(roleState.access)),
        ...(asks && ask.key === "ask" ? [ask] : []),
        ...nodes,
        ...(own && !tree.data ? [] : peopleRows(named, peoplePods, targets.person)),
      ],
      query,
    );
    return asks && ask.key !== "ask" ? [...found, ask] : found;
  }, [asks, directory, named, own, query, roleState.access, targets, tree.data]);
  // Only the assistant's row is left: nothing on a page matched the query.
  const matched = rows.filter((row) => row.kind !== "ask").length;
  // A name that finds nobody may be a person in no pod: there is nowhere to take them.
  // Under the `own` scope most people are simply not in the person's pods, so it is not said.
  const noPod = useMemo(
    () =>
      matched === 0 && targets.person && !own ? noPodNote(named, directory.pods, query) : null,
    [directory.pods, named, own, query, matched, targets.person],
  );
  const current = Math.min(active, Math.max(rows.length - 1, 0));
  const loading =
    programs.isLoading ||
    projects.isLoading ||
    workstreams.isLoading ||
    pods.isLoading ||
    (own && tree.isLoading);
  const failed =
    programs.error ??
    projects.error ??
    pods.error ??
    workstreams.error ??
    (own ? tree.error : null);

  useEffect(() => {
    list.current
      ?.querySelector<HTMLElement>(`[data-index="${current}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const go = (row: PaletteRow | undefined) => {
    if (!row) return;
    onClose();
    if (row.ask) {
      const draft = row.ask.draft;
      // After the palette has given focus back, so the assistant's field keeps it.
      window.setTimeout(() => assistant?.openWith(draft), 0);
      return;
    }
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
          <>
            {matched === 0 && query.trim() && !loading ? (
              <li className="px-4 pb-1 pt-4 text-center text-[13px] text-grey-secondary">
                Nothing on a page matches “{query.trim()}”.
                {noPod ? <span className="mt-1 block">{noPod}</span> : null}
              </li>
            ) : null}
            {rows.map((row, index) => (
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
                <RowMark row={row} />
                <span className="min-w-0 flex-1 truncate text-[15px] font-bold">
                  {row.label}
                  <StatusWords row={row} />
                </span>
                <span className="min-w-0 max-w-[45%] truncate text-right text-[12px] text-grey-secondary">
                  {row.hint}
                </span>
                {index === current ? (
                  <CornerDownLeft size={14} aria-hidden className="flex-none text-grey-secondary" />
                ) : null}
              </li>
            ))}
          </>
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
