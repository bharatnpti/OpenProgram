import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard,
  Boxes,
  FolderKanban,
  MessageSquare,
  Network,
  Settings2,
} from "lucide-react";

import { appRoles, useRole } from "./RoleContext";
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
    { to: "/me", label: "Dev Dashboard", icon: <LayoutDashboard className="h-4 w-4" /> },
    { to: "/sm", label: "SM Dashboard", icon: <LayoutDashboard className="h-4 w-4" /> },
    { to: "/po", label: "PO Dashboard", icon: <LayoutDashboard className="h-4 w-4" /> },
    { to: "/exec", label: "Exec Dashboard", icon: <LayoutDashboard className="h-4 w-4" /> },
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
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="border-b border-border bg-white lg:min-h-screen lg:border-b-0 lg:border-r">
        <div className="border-b border-border px-4 py-4">
          <div className="text-sm font-semibold">PulseOps</div>
          <div className="mt-1 text-xs text-muted-foreground">Runtime configuration console</div>
        </div>
        <div className="border-b border-border px-4 py-3">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="role-switcher">
            Active role
          </label>
          <Select
            id="role-switcher"
            className="mt-1"
            value={role}
            onChange={(event) => setRole(event.target.value as typeof role)}
          >
            {appRoles.map((item) => (
              <option key={item} value={item}>
                {item === role ? roleLabel : item}
              </option>
            ))}
          </Select>
        </div>
        <nav className="flex flex-wrap gap-1 px-2 py-3 lg:flex-col">
          {navItems
            .filter((item) => item.visible !== false)
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "inline-flex items-center gap-2 rounded px-3 py-2 text-sm transition",
                    isActive
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )
                }
              >
                {item.icon}
                {item.label}
              </NavLink>
            ))}
        </nav>
      </aside>
      <Outlet />
    </div>
  );
}
