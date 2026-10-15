import * as Dialog from "@radix-ui/react-dialog";
import { Menu, Search, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { AccountMenu } from "../components/shell/AccountMenu";
import { ActingAsControls } from "../components/shell/ActingAs";
import { CommandPalette } from "../components/shell/CommandPalette";
import { HeaderLogo } from "../components/shell/HeaderLogo";
import { TabTitle } from "../components/shell/TabTitle";
import { ViewingDateBanner, ViewingDateControl } from "../components/shell/ViewingDate";
import { Assistant } from "../features/assistant/Assistant";
import { parseHub } from "../features/hub/hubPaths";
import { cn } from "../lib/utils";
import { usePods, useProjects } from "./directory";
import { shownGroups } from "./nav";
import { RouteGuard } from "./RouteGuard";
import { useRole } from "./role";
import { useViewingDate } from "./viewingDate";

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);

/**
 * The console's frame: the header (logo, Jump to… ⌘K, the viewing day,
 * acting-as on a local tenant, the avatar), the grouped sidebar on a wide
 * screen and behind the menu button on a narrower one, and the page.
 */
export function Layout() {
  const roleState = useRole();
  const { asOf } = useViewingDate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();
  const header = useRef<HTMLElement>(null);
  const [headerHeight, setHeaderHeight] = useState(64);

  // The sidebar sits under the header, whose height grows with the past-day banner.
  useLayoutEffect(() => {
    const element = header.current;
    if (!element) return;
    const measure = () => setHeaderHeight(element.getBoundingClientRect().height);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // A page opened from the menu closes it.
  useEffect(() => setMenuOpen(false), [pathname]);

  // ⌘K on a Mac, Ctrl+K elsewhere, from anywhere in the console.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    // The assistant's button floats at the bottom right: the room it takes is set here,
    // once, and read by main's padding and the pages' sticky side panels (index.css).
    // The header's height is a variable too, for a page's own sticky row (the Project page's index).
    // .op-shell is the dark theme's reach (index.css): the header, the sidebar and the page turn
    // together, on the document's data-theme, and are as they were in light.
    <div
      className={cn(
        "op-shell min-h-screen bg-(--op-page)",
        roleState.access.assistant && "op-float-room",
      )}
      style={{ "--op-header-h": `${headerHeight}px` } as CSSProperties}
    >
      <TabTitle />
      <header
        ref={header}
        className="sticky top-0 z-30 border-b border-grey-border bg-(--op-viz-surface)"
      >
        <div className="mx-auto flex max-w-[1480px] items-center gap-x-3 px-4 py-2.5 sm:px-6">
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            aria-label="Open the menu"
            aria-expanded={menuOpen}
            aria-controls="op-menu"
            className="grid h-10 w-10 flex-none place-items-center rounded-full border border-grey-border text-ink hover:bg-grey-fill lg:hidden"
          >
            <Menu size={18} aria-hidden />
          </button>
          <NavLink
            to="/today"
            className="flex min-w-0 items-center gap-3 py-1 text-ink no-underline"
          >
            <HeaderLogo />
            <span className="min-w-0 leading-tight max-[379px]:hidden">
              <span className="block text-[16px] font-extrabold">OpenProgram</span>
              <span className="block text-[11px] text-grey-secondary">Delivery intelligence</span>
            </span>
          </NavLink>
          <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2 py-1">
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              aria-label="Jump to a screen, person, project or pod"
              aria-keyshortcuts={IS_MAC ? "Meta+K" : "Control+K"}
              className="flex h-10 flex-none items-center gap-2 rounded-full border border-grey-border bg-grey-fill px-3 text-[13px] text-grey-secondary hover:border-grey-disabled"
            >
              <Search size={15} aria-hidden />
              <span className="hidden lg:inline">Jump to…</span>
              <kbd className="hidden rounded-md border border-grey-border bg-(--op-viz-surface) px-1.5 text-[11px] lg:inline">
                {IS_MAC ? "⌘K" : "Ctrl K"}
              </kbd>
            </button>
            {/* Below sm the day picker lives in the account menu; the banner says when it's past. */}
            <div className="hidden sm:block">
              <ViewingDateControl />
            </div>
            {/* Below sm the acting-as controls live in the account menu too. */}
            {roleState.isDevMode && roleState.people.length > 0 ? (
              <div className="hidden sm:block">
                <ActingAsControls idPrefix="header" />
              </div>
            ) : null}
            <AccountMenu />
          </div>
        </div>
        <ViewingDateBanner />
      </header>
      <div className="mx-auto flex max-w-[1480px]">
        <aside
          className="sticky hidden w-[236px] flex-none self-start overflow-y-auto border-r border-grey-border px-3 py-6 lg:block"
          style={{ top: headerHeight, height: `calc(100vh - ${headerHeight}px)` }}
        >
          <SideNav />
        </aside>
        {/* Room at the bottom for the assistant's button, so it never covers the last row. */}
        <main className="min-w-0 flex-1 px-4 py-8 pb-[calc(2rem+var(--op-float-room))] sm:px-8">
          {/* An old address opens where it lives now; a page this role is not offered, the closest it has. */}
          <RouteGuard>
            {/* A different day remounts the screen, so every query on it asks for that day. */}
            <Outlet key={asOf ?? "today"} />
          </RouteGuard>
        </main>
      </div>
      <Dialog.Root open={menuOpen} onOpenChange={setMenuOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 lg:hidden" />
          <Dialog.Content
            id="op-menu"
            aria-describedby={undefined}
            className="op-shell fixed inset-y-0 left-0 z-50 w-[min(86vw,300px)] overflow-y-auto bg-(--op-viz-surface) px-3 pb-8 pt-3 shadow-op-palette lg:hidden"
          >
            <div className="mb-4 flex items-center justify-between px-1">
              <Dialog.Title className="text-[16px] font-extrabold">Menu</Dialog.Title>
              <Dialog.Close
                aria-label="Close the menu"
                className="grid h-10 w-10 place-items-center rounded-full text-ink hover:bg-grey-fill"
              >
                <X size={18} aria-hidden />
              </Dialog.Close>
            </div>
            <SideNav />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <Assistant />
    </div>
  );
}

/**
 * The sidebar's groups, each item named by the question it answers, only what
 * the role is offered (app/nav.ts). The project or pod open on the page shows
 * as one small line under its item, for orientation; the nav itself never
 * lists projects.
 */
function SideNav() {
  const { access } = useRole();
  const { pathname } = useLocation();
  const hub = parseHub(pathname);
  const projects = useProjects();
  const pods = usePods();
  // The project open in the hub, and its pod, each one line under Projects.
  const openNames = (page: string): string[] => {
    if (page !== "projects" || !hub || hub.place.level === "all") return [];
    const { place } = hub;
    const project = projects.data?.find((item) => item.id === place.projectId)?.name;
    const pod =
      place.level === "pod" ? pods.data?.find((item) => item.id === place.podId)?.name : undefined;
    return [project, pod].filter((name): name is string => Boolean(name));
  };
  return (
    <nav aria-label="Main" className="grid gap-5">
      {shownGroups(access).map((group) => (
        <div key={group.id}>
          <p className="px-3 pb-1.5 text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
            {group.label}
          </p>
          <ul className="grid gap-0.5">
            {group.items.map((item) => {
              const names = openNames(item.page);
              // Projects is the hub: it stays lit at every level, All projects included.
              const lit = item.page === "projects" && hub !== null;
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    className={({ isActive }) =>
                      cn(
                        "relative flex min-h-10 items-center rounded-xl px-3 py-2 text-[14px] font-bold no-underline",
                        isActive || lit
                          ? "bg-grey-fill text-ink before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-magenta"
                          : "text-grey-body hover:bg-grey-fill hover:text-ink",
                      )
                    }
                  >
                    {item.label}
                  </NavLink>
                  {names.map((name, index) => (
                    <p
                      key={`${index}:${name}`}
                      className="truncate py-0.5 pr-2 text-[12.5px] text-grey-secondary"
                      style={{ paddingLeft: `${24 + index * 12}px` }}
                    >
                      {name}
                    </p>
                  ))}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
