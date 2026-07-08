import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  BriefcaseBusiness,
  Activity,
  FolderKanban,
  GitBranch,
  LogOut,
  MessageSquare,
  Network,
  Handshake,
  Settings2,
  ShieldAlert,
  UserRoundCog,
} from "lucide-react";

import { appRoles, roleLabels, useRole, type AppRole } from "./role";
import { Button } from "../components/ui/button";
import { Select } from "../components/ui/select";
import { cn } from "../lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: ReactNode;
  visible?: boolean;
};

export function Layout() {
  const {
    role,
    setRole,
    roleLabel,
    roles,
    user,
    isDevMode,
    canAccessPortfolio,
    canAccessAdmin,
    canAccessRole,
    logout,
  } = useRole();
  const canAccessChatSimulator =
    canAccessAdmin &&
    (import.meta.env.DEV || import.meta.env.VITE_ENABLE_CHAT_SIMULATOR === "true");

  const navItems: NavItem[] = [
    {
      to: "/me",
      label: "Developer",
      icon: <BarChart3 className="h-4 w-4" />,
      visible: canAccessRole("dev"),
    },
    {
      to: "/sm",
      label: "Scrum Master",
      icon: <BarChart3 className="h-4 w-4" />,
      visible: canAccessRole("sm"),
    },
    {
      to: "/po",
      label: "Product Owner",
      icon: <BarChart3 className="h-4 w-4" />,
      visible: canAccessRole("po"),
    },
    {
      to: "/mgr",
      label: "Manager",
      icon: <BriefcaseBusiness className="h-4 w-4" />,
      visible: canAccessRole("mgr"),
    },
    {
      to: "/exec",
      label: "Executive",
      icon: <Network className="h-4 w-4" />,
      visible: canAccessRole("exec"),
    },
    { to: "/pods", label: "Pods", icon: <Boxes className="h-4 w-4" /> },
    { to: "/projects", label: "Projects", icon: <FolderKanban className="h-4 w-4" /> },
    { to: "/workstreams", label: "Workstreams", icon: <GitBranch className="h-4 w-4" /> },
    {
      to: "/flow",
      label: "Flow",
      icon: <Activity className="h-4 w-4" />,
      visible: canAccessPortfolio,
    },
    {
      to: "/portfolio",
      label: "Portfolio",
      icon: <Network className="h-4 w-4" />,
      visible: canAccessPortfolio,
    },
    {
      to: "/risks",
      label: "Risks",
      icon: <ShieldAlert className="h-4 w-4" />,
      visible: canAccessPortfolio,
    },
    {
      to: "/cross-person-requests",
      label: "Requests",
      icon: <Handshake className="h-4 w-4" />,
      visible: canAccessPortfolio,
    },
    {
      to: "/admin",
      label: "Admin Config",
      icon: <Settings2 className="h-4 w-4" />,
      visible: canAccessAdmin,
    },
    {
      to: "/mock-slack",
      label: "Mock Slack",
      icon: <MessageSquare className="h-4 w-4" />,
      visible: canAccessChatSimulator,
    },
  ];

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[264px_minmax(0,1fr)]">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:shadow-panel"
      >
        Skip to content
      </a>
      <aside className="border-b border-border bg-surface/95 shadow-sm lg:sticky lg:top-0 lg:min-h-screen lg:border-b-0 lg:border-r">
        <div className="border-b border-border px-4 py-4">
          <div className="flex items-center gap-2">
            <div className="grid h-8 w-8 place-items-center rounded-md bg-primary text-sm font-semibold text-primary-foreground">
              P
            </div>
            <div>
              <div className="text-sm font-semibold">OpenProgram</div>
              <div className="text-xs text-muted-foreground">Operational delivery console</div>
            </div>
          </div>
        </div>
        {isDevMode ? (
          <div className="border-b border-border px-4 py-3">
            <div className="flex items-center gap-2">
              <UserRoundCog className="h-4 w-4 text-muted-foreground" />
              <label className="text-xs font-medium text-muted-foreground" htmlFor="role-switcher">
                Active role
              </label>
            </div>
            <Select id="role-switcher" className="mt-1" value={role} onChange={onRoleChange}>
              {appRoles.map((item) => (
                <option key={item} value={item}>
                  {roleLabels[item]}
                </option>
              ))}
            </Select>
            <p className="mt-2 text-xs text-muted-foreground">
              Viewing as <span className="font-medium text-foreground">{roleLabel}</span>
            </p>
          </div>
        ) : (
          <div className="border-b border-border px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <UserRoundCog className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <p className="truncate text-xs font-medium text-muted-foreground">
                    {user?.name ?? user?.email ?? user?.username ?? user?.subject}
                  </p>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {roles.map((item) => roleLabels[item]).join(", ") || "No app roles"}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Sign out"
                title="Sign out"
                onClick={() => {
                  void logout();
                }}
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
        <nav className="flex gap-1 overflow-x-auto px-2 py-3 lg:flex-col" aria-label="Primary">
          {navItems
            .filter((item) => item.visible !== false)
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "inline-flex min-h-9 shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
                  )
                }
              >
                {item.icon}
                {item.label}
              </NavLink>
            ))}
        </nav>
      </aside>
      <div id="main-content" className="min-w-0">
        <Outlet />
      </div>
    </div>
  );

  function onRoleChange(event: React.ChangeEvent<HTMLSelectElement>) {
    setRole(event.target.value as AppRole);
  }
}
