import { NavLink, Outlet } from "react-router-dom";

import { cn } from "../lib/utils";
import { NAV } from "./nav";
import { initialsFor, roleLabels, useRole, type AppRole } from "./role";

export function Layout() {
  const roleState = useRole();

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-30 border-b border-grey-border bg-white">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center gap-x-6 gap-y-2 px-4 pt-3 sm:px-8">
          <NavLink to="/today" className="flex items-center gap-3 py-1 text-ink no-underline">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-magenta" aria-hidden>
              <svg
                viewBox="0 0 20 20"
                className="h-5 w-5"
                fill="none"
                stroke="#fff"
                strokeWidth="2"
              >
                <circle cx="10" cy="4" r="2" />
                <circle cx="4" cy="16" r="2" />
                <circle cx="16" cy="16" r="2" />
                <path d="M10 6v4M10 10l-5 4M10 10l5 4" strokeLinecap="round" />
              </svg>
            </span>
            <span className="leading-tight">
              <span className="block text-[16px] font-extrabold">OpenProgram</span>
              <span className="block text-[11px] text-grey-secondary">Delivery intelligence</span>
            </span>
          </NavLink>
          <div className="ml-auto flex items-center gap-3 py-1">
            <IdentityControls />
          </div>
          <nav
            aria-label="Main"
            className="-mx-1 flex w-full gap-1 overflow-x-auto px-1 [scrollbar-width:none]"
          >
            {NAV.filter((item) => item.to !== "/chat" || roleState.chatEnabled).map((item) =>
              item.offered(roleState) ? (
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
              ) : (
                <span
                  key={item.to}
                  title={`Not offered to the ${roleState.roleLabel.toLowerCase()} role`}
                  className="flex-none px-3 pb-3 pt-2 text-[15px] font-bold text-grey-disabled line-through"
                >
                  {item.label}
                </span>
              ),
            )}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-8">
        <Outlet />
      </main>
    </div>
  );
}

/**
 * Local demo tenants act as any seeded person, with a lens for people who hold
 * several roles; under real sign-in the token decides, so only the name, the
 * role and Sign out remain.
 */
function IdentityControls() {
  const {
    displayName,
    isDevMode,
    people,
    actingAs,
    setActingAsId,
    roles,
    role,
    setRole,
    roleLabel,
    logout,
  } = useRole();

  const avatar = (
    <span
      className="grid h-9 w-9 flex-none place-items-center rounded-full bg-ink text-[12px] font-extrabold text-white"
      aria-hidden
    >
      {initialsFor(displayName)}
    </span>
  );

  if (isDevMode && people.length > 0) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {avatar}
        <label htmlFor="acting-as" className="sr-only">
          Acting as
        </label>
        <select
          id="acting-as"
          className="h-10 max-w-[230px] rounded-full border border-grey-border bg-white px-3 text-[13px] font-bold"
          value={actingAs?.id ?? ""}
          onChange={(event) => setActingAsId(event.target.value)}
        >
          {people.map((person) => (
            <option key={person.id} value={person.id}>
              {person.name}
              {person.title ? ` · ${person.title}` : ""}
            </option>
          ))}
        </select>
        {roles.length > 1 ? (
          <>
            <label htmlFor="lens" className="sr-only">
              View as
            </label>
            <select
              id="lens"
              className="h-10 rounded-full border border-grey-border bg-white px-3 text-[13px]"
              value={role}
              onChange={(event) => setRole(event.target.value as AppRole)}
            >
              {roles.map((item) => (
                <option key={item} value={item}>
                  {roleLabels[item]}
                </option>
              ))}
            </select>
          </>
        ) : (
          <span className="text-[12px] text-grey-secondary">{roleLabel}</span>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      {avatar}
      <span className="text-[13px] leading-tight">
        <span className="block font-bold">{displayName}</span>
        <span className="block text-[11px] text-grey-secondary">{roleLabel}</span>
      </span>
      {!isDevMode ? (
        <button
          type="button"
          className="ml-1 text-[13px] font-bold text-magenta"
          onClick={() => void logout()}
        >
          Sign out
        </button>
      ) : null}
    </div>
  );
}
