import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  BriefcaseBusiness,
  FolderKanban,
  MessageSquare,
  Network,
  Settings2,
  UserRoundCog,
} from "lucide-react";

import { appRoles, roleLabels, useRole, type AppRole } from "./role";
import { Select } from "../components/ui/select";
import { cn } from "../lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: ReactNode;
  visible?: boolean;
};

export function Layout() {
  const { role, setRole, roleLabel, canAccessPortfolio, canAccessAdmin } = useRole();
  const canAccessChatSimulator =
    canAccessAdmin &&
    (import.meta.env.DEV || import.meta.env.VITE_ENABLE_CHAT_SIMULATOR === "true");

  const navItems: NavItem[] = [
    { to: "/me", label: "Developer", icon: <BarChart3 className="h-4 w-4" /> },
    { to: "/sm", label: "Scrum Master", icon: <BarChart3 className="h-4 w-4" /> },
    { to: "/po", label: "Product Owner", icon: <BarChart3 className="h-4 w-4" /> },
    { to: "/mgr", label: "Manager", icon: <BriefcaseBusiness className="h-4 w-4" /> },
    { to: "/exec", label: "Executive", icon: <Network className="h-4 w-4" /> },
    { to: "/pods", label: "Pods", icon: <Boxes className="h-4 w-4" /> },
    { to: "/projects", label: "Projects", icon: <FolderKanban className="h-4 w-4" /> },
    {
      to: "/portfolio",
      label: "Portfolio",
      icon: <Network className="h-4 w-4" />,
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
              <div className="text-sm font-semibold">PulseOps</div>
              <div className="text-xs text-muted-foreground">Operational delivery console</div>
            </div>
          </div>
        </div>
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
