import { Menu, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useViewingDate } from "../../app/viewingDate";
import { cn } from "../../lib/utils";
import { ViewingDateControl } from "./ViewingDate";

export type NavItem = { label: string; to: string; needsAggregate?: boolean };

/**
 * The primary navigation where the header has no room for it: below lg, one
 * button naming the current page opens the pages, the search, and (below sm,
 * where the header drops it) the viewing date.
 *
 * A dot on the button says a past day is being viewed while the date control
 * is folded away; the banner above every page says so too.
 */
export function NavMenu({ items, onOpenPalette }: { items: NavItem[]; onOpenPalette: () => void }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();
  const { isPast, applies } = useViewingDate();
  const current = items.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`));

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="relative min-w-0 lg:hidden" ref={containerRef}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="primary-menu"
        aria-label={current ? `Menu, on ${current.label}` : "Menu"}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex h-12 items-center gap-2 rounded-full border bg-white px-3.5 text-ink",
          open ? "border-magenta" : "border-grey-border hover:border-grey-disabled",
        )}
      >
        <Menu size={18} className="shrink-0" />
        <span className="hidden max-w-[96px] truncate text-[15px] font-bold min-[360px]:inline sm:max-w-none">
          {current?.label ?? "Menu"}
        </span>
        {isPast && applies ? (
          <span
            aria-hidden
            title="Viewing a past day"
            className="h-2 w-2 shrink-0 rounded-full bg-rag-amber sm:hidden"
          />
        ) : null}
      </button>

      {open ? (
        <div
          id="primary-menu"
          className="animate-op-pop fixed inset-x-4 top-[76px] z-50 rounded-2xl border border-grey-border bg-white p-2 shadow-op-menu sm:absolute sm:inset-x-auto sm:left-0 sm:top-14 sm:w-[280px]"
        >
          <nav aria-label="Primary" className="flex flex-col">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  cn(
                    "rounded-lg px-3.5 py-2.5 text-[15px] no-underline hover:bg-grey-fill",
                    isActive ? "bg-grey-fill font-bold text-ink" : "font-medium text-grey-body",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="mt-1.5 border-t border-grey-fill pt-1.5">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onOpenPalette();
              }}
              className="flex w-full items-center gap-2.5 rounded-lg px-3.5 py-2.5 text-left text-sm text-grey-secondary hover:bg-grey-fill"
            >
              <Search size={16} />
              Search or jump to…
            </button>
          </div>
          {applies ? (
            <div className="mt-1.5 border-t border-grey-fill px-1.5 pb-1 pt-2.5 sm:hidden">
              <ViewingDateControl />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
