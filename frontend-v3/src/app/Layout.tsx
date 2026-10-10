import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { AccountMenu } from "../components/shell/AccountMenu";
import { ActingAsControls } from "../components/shell/ActingAs";
import { CommandPalette } from "../components/shell/CommandPalette";
import { HeaderLogo } from "../components/shell/HeaderLogo";
import { TabTitle } from "../components/shell/TabTitle";
import { ViewingDateBanner, ViewingDateControl } from "../components/shell/ViewingDate";
import { Assistant } from "../features/assistant/Assistant";
import { cn } from "../lib/utils";
import { shownNav } from "./nav";
import { RouteGuard } from "./RouteGuard";
import { useRole } from "./role";
import { useViewingDate } from "./viewingDate";

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);

export function Layout() {
  const roleState = useRole();
  const { asOf } = useViewingDate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const nav = useRef<HTMLElement>(null);
  const { pathname } = useLocation();

  // On a phone an admin's seven tabs do not fit and scroll: keep the one shown in
  // view (Admin is the last, and would otherwise sit off the edge).
  useEffect(() => {
    nav.current
      ?.querySelector<HTMLElement>('[aria-current="page"]')
      ?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [pathname]);

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
    <div className="min-h-screen bg-white">
      <TabTitle />
      <header className="sticky top-0 z-30 border-b border-grey-border bg-white">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center gap-x-6 gap-y-2 px-4 pt-3 sm:px-8">
          <NavLink to="/today" className="flex items-center gap-3 py-1 text-ink no-underline">
            <HeaderLogo />
            <span className="leading-tight">
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
              <kbd className="hidden rounded-md border border-grey-border bg-white px-1.5 text-[11px] lg:inline">
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
          <nav
            ref={nav}
            aria-label="Main"
            // Below md the tabs scroll sideways: the right edge fades out to say there are more.
            className="-mx-1 flex w-full gap-1 overflow-x-auto px-1 [scrollbar-width:none] max-md:[mask-image:linear-gradient(to_right,black_calc(100%-28px),transparent)]"
          >
            {shownNav(roleState.access).map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "relative flex-none px-3 pb-3 pt-2 text-[15px] font-bold no-underline",
                    isActive
                      ? "text-ink after:absolute after:inset-x-3 after:bottom-0 after:h-[3px] after:rounded-full after:bg-magenta"
                      : "text-grey-body hover:text-ink",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <ViewingDateBanner />
      </header>
      {/* Room at the bottom for the assistant's button, so it never covers the last row. */}
      <main
        className={cn(
          "mx-auto max-w-[1240px] px-4 py-8 sm:px-8",
          roleState.access.assistant && "pb-28",
        )}
      >
        {/* A link to a page this role is not offered opens the closest one it has. */}
        <RouteGuard>
          {/* A different day remounts the screen, so every query on it asks for that day. */}
          <Outlet key={asOf ?? "today"} />
        </RouteGuard>
      </main>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <Assistant />
    </div>
  );
}
