import * as Dialog from "@radix-ui/react-dialog";
import { Check, ChevronDown, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { podPath } from "../../app/links";
import { RagDot } from "../../components/ui/Bits";
import { cn } from "../../lib/utils";
import { switcherSections, type ProjectOption } from "../project/projectChoice";

/** Past this many matches the list stops and asks for a narrower search. */
const SHOWN_OPTIONS = 100;

/**
 * The Pod page's title, which opens the pod switcher (D86): a search over the
 * pods the person may open, theirs first, then the ones opened lately in this
 * browser, then the rest under their project, worst first. Typing filters by
 * pod or project name; the arrow keys move, Enter opens, Escape closes. A pick
 * keeps the page's section (the hash) and the viewing day. On a phone the list
 * is a full-screen sheet. With one pod to open, the title is only a title.
 */
export function PodSwitcher({
  current,
  options,
  projects,
  recent,
}: {
  current: { id: string; name: string };
  options: ProjectOption[];
  /** The projects, worst first: the pods' headings, and what a search finds them by. */
  projects: { id: string; name: string }[];
  recent: string[];
}) {
  const [open, setOpen] = useState(false);
  const single = options.length <= 1 && options.some((option) => option.id === current.id);
  const title = "text-[30px] font-extrabold tracking-tight max-sm:text-[26px]";
  if (single) return <h1 className={cn(title, "text-balance")}>{current.name}</h1>;
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <h1 className={cn(title, "min-w-0")}>
        <Dialog.Trigger
          aria-label={`${current.name}. Switch pod`}
          className="group -mx-1 inline-flex items-center gap-2 rounded-2xl px-1 text-left hover:bg-grey-fill"
        >
          <span className="min-w-0">{current.name}</span>
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
          className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-white shadow-op-palette sm:inset-auto sm:left-1/2 sm:top-[10vh] sm:w-[min(92vw,560px)] sm:-translate-x-1/2 sm:rounded-3xl animate-op-pop"
        >
          <Dialog.Title className="sr-only">Switch pod</Dialog.Title>
          <SwitcherBody
            current={current.id}
            options={options}
            projects={projects}
            recent={recent}
            onClose={() => setOpen(false)}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function SwitcherBody({
  current,
  options,
  projects,
  recent,
  onClose,
}: {
  current: string;
  options: ProjectOption[];
  projects: { id: string; name: string }[];
  recent: string[];
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const sections = useMemo(
    () =>
      switcherSections(options, recent, projects, query).map((section) =>
        // A pod in no project: the project switcher's catch-all, said of pods.
        section.id === "program:"
          ? { ...section, label: section.label === "All projects" ? "All pods" : "Other pods" }
          : section,
      ),
    [options, projects, query, recent],
  );
  const flat = useMemo(() => sections.flatMap((section) => section.options), [sections]);
  const shown = flat.slice(0, SHOWN_OPTIONS);
  const more = flat.length - shown.length;
  const groups = useMemo(() => {
    let at = 0;
    return sections
      .map((section) => ({
        ...section,
        rows: section.options
          .map((option) => ({ option, at: at++ }))
          .filter((row) => row.at < SHOWN_OPTIONS),
      }))
      .filter((group) => group.rows.length > 0);
  }, [sections]);
  const index = Math.min(active, Math.max(shown.length - 1, 0));

  useEffect(() => {
    list.current
      ?.querySelector<HTMLElement>(`[data-index="${index}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [index]);

  const go = (option: ProjectOption | undefined) => {
    if (!option) return;
    onClose();
    if (option.id === current) return;
    // The section stays (the hash); the viewing day is the shell's and comes along.
    navigate({ pathname: podPath(option.id), hash: location.hash });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const last = shown.length - 1;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(index >= last ? 0 : index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(index <= 0 ? last : index - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(shown[index]);
    }
  };

  return (
    <>
      <div className="flex items-center gap-3 border-b border-grey-border px-5 py-4">
        <Search size={18} aria-hidden className="flex-none text-grey-secondary" />
        <input
          role="combobox"
          aria-expanded
          aria-controls="pod-switcher-list"
          aria-activedescendant={shown.length > 0 ? `pod-option-${index}` : undefined}
          aria-autocomplete="list"
          aria-label="Find a pod by its name or its project"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Find a pod…"
          className="min-w-0 flex-1 bg-transparent text-[17px] text-ink outline-none placeholder:text-grey-secondary"
        />
        <Dialog.Close className="flex-none rounded-md border border-grey-border px-2 py-0.5 text-[11px] text-grey-secondary sm:hidden">
          Close
        </Dialog.Close>
        <kbd className="hidden flex-none rounded-md border border-grey-border px-2 py-0.5 text-[11px] text-grey-secondary sm:inline">
          Esc
        </kbd>
      </div>
      <ul
        ref={list}
        id="pod-switcher-list"
        role="listbox"
        aria-label="Pods"
        className="min-h-0 flex-1 overflow-y-auto p-2 sm:max-h-[min(60vh,520px)] sm:flex-none"
      >
        {shown.length === 0 ? (
          <li className="px-4 py-8 text-center text-[14px] text-grey-secondary">
            No pod matches “{query.trim()}”.
          </li>
        ) : (
          groups.map((section) => (
            <li key={section.id} role="presentation" className="mb-2 last:mb-0">
              <p
                id={`pod-group-${section.id}`}
                className="px-4 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wider text-grey-secondary"
              >
                {section.label}
              </p>
              <ul role="group" aria-labelledby={`pod-group-${section.id}`}>
                {section.rows.map(({ option, at }) => {
                  const project = section.id.startsWith("program:")
                    ? null
                    : projects.find((p) => p.id === option.programIds[0])?.name;
                  return (
                    <li
                      key={option.id}
                      id={`pod-option-${at}`}
                      data-index={at}
                      role="option"
                      aria-selected={at === index}
                      aria-current={option.id === current ? "page" : undefined}
                      onMouseMove={() => setActive(at)}
                      onClick={() => go(option)}
                      className={cn(
                        "flex min-h-11 cursor-pointer items-center gap-3 rounded-xl px-4 py-2",
                        at === index ? "bg-grey-fill" : "hover:bg-grey-fill",
                      )}
                    >
                      {option.rag !== null ? <RagDot rag={option.rag} /> : null}
                      <span className="min-w-0 flex-1 truncate text-[15px] font-bold text-ink">
                        {option.name}
                      </span>
                      {project ? (
                        <span className="min-w-0 max-w-[45%] truncate text-right text-[12px] text-grey-secondary">
                          {project}
                        </span>
                      ) : null}
                      {option.id === current ? (
                        <Check size={15} aria-hidden className="flex-none text-ink" />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))
        )}
      </ul>
      <p className="flex flex-wrap gap-x-4 border-t border-grey-border px-5 py-2.5 text-[12px] text-grey-secondary">
        <span>↑ ↓ to move · Enter to open · Esc to close</span>
        {more > 0 ? <span>{more} more: type to narrow the list.</span> : null}
      </p>
    </>
  );
}
