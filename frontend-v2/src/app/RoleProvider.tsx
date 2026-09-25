import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { apiClient, setActingAs } from "../api/client";
import type { AuthStatusResponse, DevUserResponse } from "../api/schema";
import {
  appRolesOf,
  highestRole,
  isAppRole,
  readStoredRole,
  readStoredUserId,
  roleLabels,
  RoleContext,
  STORAGE_KEY,
  USER_STORAGE_KEY,
  type AppRole,
  type RoleContextValue,
} from "./role";

const routeByRole: Record<AppRole, string> = {
  dev: "/today",
  sm: "/today",
  po: "/today",
  mgr: "/today",
  exec: "/today",
  admin: "/today",
};

export function RoleProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [role, setRoleState] = useState<AppRole>(readStoredRole);
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [people, setPeople] = useState<DevUserResponse[]>([]);
  const [peopleLoading, setPeopleLoading] = useState(true);
  const [actingAsId, setActingAsIdState] = useState<string | null>(readStoredUserId);

  const actingAsPerson = useMemo(
    () => people.find((person) => person.id === actingAsId) ?? null,
    [actingAsId, people],
  );

  const personRoles = useMemo(() => appRolesOf(actingAsPerson), [actingAsPerson]);
  // While a person is selected the lens must be one of their own roles; before
  // people load, the stored role stands in.
  const activeRole = useMemo(() => {
    if (personRoles.length === 0) {
      return role;
    }
    return personRoles.includes(role) ? role : highestRole(personRoles);
  }, [personRoles, role]);

  // Identity is installed on the shared client during render, not in an effect,
  // so the first request a newly mounted screen makes already carries it.
  setActingAs(actingAsPerson ? { id: actingAsPerson.id, roles: [activeRole] } : null);

  // Every cached response belongs to whoever was being acted as when it was
  // fetched, so switching person drops the cache rather than showing one
  // person's day under another person's name.
  const previousIdentity = useRef<string | null>(null);
  useEffect(() => {
    const identity = actingAsPerson ? `${actingAsPerson.id}:${activeRole}` : null;
    if (previousIdentity.current !== null && previousIdentity.current !== identity) {
      queryClient.clear();
    }
    previousIdentity.current = identity;
  }, [actingAsPerson, activeRole, queryClient]);

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
            demo_mode: false,
            chat_enabled: false,
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

  // Only ask for switchable people once the backend has said it serves them.
  const demoMode = authStatus?.demo_mode === true;
  useEffect(() => {
    if (authLoading) {
      return;
    }
    if (!demoMode) {
      setPeople([]);
      setPeopleLoading(false);
      return;
    }
    let cancelled = false;
    apiClient
      .devUsers()
      .then((response) => {
        if (cancelled) {
          return;
        }
        setPeople(response.items);
        setActingAsIdState((current) => {
          if (current && response.items.some((person) => person.id === current)) {
            return current;
          }
          // The list arrives most-senior first, so a fresh browser opens on the
          // portfolio view rather than one developer's own day.
          const next = response.items[0]?.id ?? null;
          if (next) {
            localStorage.setItem(USER_STORAGE_KEY, next);
          }
          return next;
        });
      })
      .catch(() => {
        if (!cancelled) {
          setPeople([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPeopleLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [authLoading, demoMode]);

  const value = useMemo<RoleContextValue>(() => {
    const provider = authStatus?.provider ?? "dev";
    const isDevMode = provider === "dev";
    const oidcRoles = authStatus?.user?.roles.filter(isAppRole) ?? [];
    // In dev mode the selected person owns the role set; under OIDC the token
    // does, and the person picker is absent.
    const activeRoles = isDevMode ? (personRoles.length > 0 ? personRoles : [role]) : oidcRoles;
    const selectedRole = isDevMode ? activeRole : highestRole(activeRoles);
    const authenticated = isDevMode || authStatus?.authenticated === true;
    const roleSet = new Set(activeRoles);
    const hasAdmin = roleSet.has("admin");

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
      demoMode: authStatus?.demo_mode === true,
      chatEnabled: authStatus?.chat_enabled === true,
      canAccessPortfolio: hasAdmin || roleSet.has("mgr") || roleSet.has("exec"),
      canReadAggregate:
        hasAdmin ||
        roleSet.has("exec") ||
        roleSet.has("mgr") ||
        roleSet.has("po") ||
        roleSet.has("sm"),
      canAccessAdmin: hasAdmin,
      canAccessRole: (nextRole: AppRole) =>
        isDevMode ? roleSet.has(nextRole) : hasAdmin || roleSet.has(nextRole),
      defaultRoute: routeByRole[selectedRole],
      signIn: () => {
        window.location.assign(authStatus?.login_url ?? "/api/v1/auth/login?return_url=/");
      },
      logout: async () => {
        const response = await apiClient.logout();
        setAuthStatus(null);
        window.location.assign(response.redirect_url);
      },
      people,
      peopleLoading,
      actingAs: actingAsPerson,
      setActingAsId: (id) => {
        setActingAsIdState(id);
        localStorage.setItem(USER_STORAGE_KEY, id);
        // A different person means a different lens, so any stored override is
        // dropped and the new person's own highest role applies.
        localStorage.removeItem(STORAGE_KEY);
        const next = people.find((person) => person.id === id) ?? null;
        setRoleState(highestRole(appRolesOf(next)));
      },
      displayName:
        actingAsPerson?.name ??
        authStatus?.user?.name ??
        authStatus?.user?.username ??
        "OpenProgram",
    };
  }, [
    actingAsPerson,
    activeRole,
    authLoading,
    authStatus,
    people,
    peopleLoading,
    personRoles,
    role,
  ]);

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}
