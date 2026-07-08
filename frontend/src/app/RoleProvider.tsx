import { useEffect, useMemo, useState, type ReactNode } from "react";

import { apiClient } from "../api/client";
import type { AuthStatusResponse } from "../api/schema";
import {
  appRoles,
  readStoredRole,
  roleLabels,
  RoleContext,
  STORAGE_KEY,
  type AppRole,
  type RoleContextValue,
} from "./role";

const routeByRole: Record<AppRole, string> = {
  dev: "/me",
  sm: "/sm",
  po: "/po",
  mgr: "/mgr",
  exec: "/exec",
  admin: "/admin",
};

const rolePriority: AppRole[] = ["admin", "exec", "mgr", "po", "sm", "dev"];

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<AppRole>(readStoredRole);
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [authLoading, setAuthLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .authStatus()
      .then((status) => {
        if (!cancelled) {
          setAuthStatus(status);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setAuthStatus({
            authenticated: false,
            provider: "oidc_bff",
            login_url: "/api/v1/auth/login?return_url=/",
          });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAuthLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<RoleContextValue>(() => {
    const provider = authStatus?.provider ?? "dev";
    const isDevMode = provider === "dev";
    const oidcRoles = authStatus?.user?.roles.filter(isAppRole) ?? [];
    const activeRoles = isDevMode ? [role] : oidcRoles;
    const selectedRole = isDevMode ? role : highestRole(activeRoles);
    const authenticated = isDevMode || authStatus?.authenticated === true;
    const roleSet = new Set(activeRoles);
    const hasAdmin = roleSet.has("admin");
    const canAccessRole = (nextRole: AppRole) => isDevMode || hasAdmin || roleSet.has(nextRole);

    return {
      role: selectedRole,
      setRole: (nextRole) => {
        if (!isDevMode) {
          return;
        }
        setRoleState(nextRole);
        localStorage.setItem(STORAGE_KEY, nextRole);
      },
      roleLabel: roleLabels[selectedRole],
      roles: activeRoles,
      provider,
      authenticated,
      authLoading,
      loginUrl: authStatus?.login_url ?? null,
      user: authStatus?.user ?? null,
      isDevMode,
      canAccessPortfolio: isDevMode
        ? role === "mgr" || role === "exec" || role === "admin"
        : hasAdmin || roleSet.has("mgr") || roleSet.has("exec"),
      canAccessAdmin: isDevMode ? role === "admin" : hasAdmin,
      canAccessRole,
      defaultRoute: routeByRole[selectedRole],
      signIn: () => {
        window.location.assign(authStatus?.login_url ?? "/api/v1/auth/login?return_url=/");
      },
      logout: async () => {
        const response = await apiClient.logout();
        setAuthStatus(null);
        window.location.assign(response.redirect_url);
      },
    };
  }, [authLoading, authStatus, role]);

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

function isAppRole(value: string): value is AppRole {
  return appRoles.includes(value as AppRole);
}

function highestRole(roles: AppRole[]): AppRole {
  return rolePriority.find((item) => roles.includes(item)) ?? "dev";
}
