import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { cn } from "../lib/utils";
import { initialsFor, roleLabels, useRole } from "./role";

const CONSOLE_URL = import.meta.env.VITE_CONSOLE_URL ?? "http://127.0.0.1:5174";

export function Layout() {
  const { projectId } = useParams();

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-30 border-b border-grey-border bg-white">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-8">
          <NavLink to="/" className="flex items-center gap-3 text-ink no-underline">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-magenta" aria-hidden>
              <svg
                viewBox="0 0 20 20"
                className="h-5 w-5"
                fill="none"
                stroke="#fff"
                strokeWidth="2"
              >
                <path d="M4 15V9M10 15V5M16 15v-3" strokeLinecap="round" />
              </svg>
            </span>
            <span className="leading-tight">
              <span className="block text-[16px] font-extrabold">OpenProgram</span>
              <span className="block text-[11px] text-grey-secondary">Project reports</span>
            </span>
          </NavLink>

          {projectId ? <ProjectNav projectId={projectId} /> : null}

          <div className="ml-auto flex flex-wrap items-center gap-3">
            <a href={CONSOLE_URL} className="text-[13px] font-bold">
              Open console
            </a>
            <IdentityControls />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1200px] px-4 py-8 sm:px-8">
        <Outlet />
      </main>
    </div>
  );
}

function ProjectNav({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const projects = useQuery({ queryKey: ["projects"], queryFn: apiClient.projects });
  const tab = (to: string, label: string) => (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "rounded-full px-4 py-2 text-[14px] font-bold no-underline",
          isActive ? "bg-ink text-white" : "text-grey-body hover:bg-grey-fill",
        )
      }
    >
      {label}
    </NavLink>
  );

  return (
    <div className="flex flex-wrap items-center gap-3">
      <label htmlFor="project-picker" className="sr-only">
        Project
      </label>
      <select
        id="project-picker"
        className="h-10 max-w-[240px] rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
        value={projectId}
        onChange={(event) => {
          const view = location.pathname.endsWith("/overall") ? "overall" : "daily";
          navigate(`/projects/${event.target.value}/${view}`);
        }}
      >
        {(projects.data ?? [{ id: projectId, name: projectId }]).map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
      <nav className="flex gap-1 rounded-full bg-grey-fill p-1" aria-label="Report view">
        {tab(`/projects/${projectId}/daily`, "Daily")}
        {tab(`/projects/${projectId}/overall`, "Overall")}
      </nav>
    </div>
  );
}

/**
 * Local demo tenants act as any seeded person; under real sign-in the token
 * decides, so only the name and a sign-out remain.
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

  if (isDevMode && people.length > 0) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="acting-as" className="sr-only">
          Acting as
        </label>
        <select
          id="acting-as"
          className="h-10 max-w-[220px] rounded-full border border-grey-border bg-white px-3 text-[13px]"
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
              onChange={(event) => setRole(event.target.value as typeof role)}
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
      <span
        className="grid h-9 w-9 place-items-center rounded-full bg-ink text-[12px] font-extrabold text-white"
        aria-hidden
      >
        {initialsFor(displayName)}
      </span>
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
