import { Check, ChevronDown, Search, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { DevUserResponse } from "../../api/schema";
import { initialsFor, roleLabels, useRole, type AppRole } from "../../app/role";
import { cn } from "../../lib/utils";

/** The order people are grouped in, most senior first. */
const GROUP_ORDER: AppRole[] = ["exec", "mgr", "po", "sm", "dev", "admin"];

/**
 * Acting-as picker for the local demo tenant.
 *
 * Choosing a person re-issues every request as them, so each role's own
 * screens can be shown from one browser without a login per person. Renders
 * nothing unless the backend reports `demo_mode`, which is off by default and
 * refused outside a local dev-auth tenant.
 */
export function PersonPicker() {
  const { people, peopleLoading, actingAs, setActingAsId, demoMode } = useRole();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    searchRef.current?.focus();
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const groups = useMemo(() => groupPeople(people, query), [people, query]);

  if (!demoMode || peopleLoading || people.length === 0) {
    return null;
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        // Below lg only the initials show, so the name rides on the label.
        aria-label={actingAs ? `Acting as ${actingAs.name}` : "Select a person"}
        title={actingAs ? `Acting as ${actingAs.name}` : "Select a person"}
        onClick={() => {
          setOpen((current) => !current);
          setQuery("");
        }}
        className={cn(
          "flex h-12 items-center gap-2.5 rounded-full border bg-white pl-2 pr-3.5 text-left",
          open ? "border-magenta" : "border-grey-border hover:border-grey-disabled",
        )}
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-grey-fill text-[12px] font-extrabold text-ink">
          {actingAs ? initialsFor(actingAs.name) : <Users size={14} />}
        </span>
        <span className="hidden min-w-0 lg:block">
          <span className="block truncate text-[14px] font-bold leading-tight">
            {actingAs?.name ?? "Select a person"}
          </span>
          <span className="block truncate text-[11px] leading-tight text-grey-secondary">
            Acting as · {actingAs ? rolesLabel(actingAs.roles) : "nobody"}
          </span>
        </span>
        <ChevronDown size={16} className="shrink-0 text-grey-secondary" />
      </button>

      {open ? (
        <div className="animate-op-pop absolute right-0 top-14 z-50 w-[340px] overflow-hidden rounded-2xl border border-grey-border bg-white shadow-op-menu">
          <div className="flex items-center gap-2 border-b border-grey-fill px-3.5 py-3">
            <Search size={15} className="shrink-0 text-grey-secondary" />
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search people…"
              aria-label="Search people"
              className="w-full bg-transparent text-[14px] outline-none placeholder:text-grey-secondary"
            />
          </div>
          <div className="max-h-[420px] overflow-y-auto py-1.5" role="listbox">
            {groups.length === 0 ? (
              <p className="px-3.5 py-6 text-center text-[13px] text-grey-secondary">
                No one matches “{query}”.
              </p>
            ) : (
              groups.map(([groupRole, members]) => (
                <div key={groupRole}>
                  <div className="px-3.5 pb-1 pt-2.5 text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
                    {roleLabels[groupRole]}
                  </div>
                  {members.map((person) => (
                    <button
                      key={person.id}
                      type="button"
                      role="option"
                      aria-selected={person.id === actingAs?.id}
                      onClick={() => {
                        setActingAsId(person.id);
                        setOpen(false);
                      }}
                      className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-grey-fill"
                    >
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-grey-fill text-[11px] font-extrabold text-ink">
                        {initialsFor(person.name)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span
                          className={cn(
                            "block truncate text-[14px] leading-tight",
                            person.id === actingAs?.id ? "font-bold text-magenta" : "font-medium",
                          )}
                        >
                          {person.name}
                        </span>
                        <span className="block truncate text-[11px] leading-tight text-grey-secondary">
                          {[person.title, (person.pods ?? []).join(", ")]
                            .filter(Boolean)
                            .join(" · ") || "No pod"}
                        </span>
                      </span>
                      {person.id === actingAs?.id ? (
                        <Check size={16} className="shrink-0 text-magenta" />
                      ) : null}
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
          <p className="border-t border-grey-fill px-3.5 py-2.5 text-[11px] leading-relaxed text-grey-secondary">
            Local demo only. Every request is re-issued as the person you pick.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function rolesLabel(roles: string[]): string {
  const labelled = roles
    .filter((role): role is AppRole => role in roleLabels)
    .map((role) => roleLabels[role]);
  return labelled.join(" + ") || "no role";
}

function groupPeople(people: DevUserResponse[], query: string): [AppRole, DevUserResponse[]][] {
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? people.filter((person) =>
        [person.name, person.title ?? "", person.email ?? "", ...(person.pods ?? [])]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      )
    : people;

  const buckets = new Map<AppRole, DevUserResponse[]>();
  for (const person of matches) {
    // A person appears once, under their most senior role.
    const primary = GROUP_ORDER.find((role) => person.roles.includes(role)) ?? "dev";
    const bucket = buckets.get(primary);
    if (bucket) {
      bucket.push(person);
    } else {
      buckets.set(primary, [person]);
    }
  }
  return GROUP_ORDER.filter((role) => buckets.has(role)).map((role) => [
    role,
    buckets.get(role) ?? [],
  ]);
}
