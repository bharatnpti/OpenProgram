import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

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
  store,
  STORAGE_KEY,
  USER_STORAGE_KEY,
  type AppRole,
  type RoleContextValue,
} from "./role";

/*
 * Ported from frontend-v2's RoleProvider. Keep the two in step: the identity
 * rules (who the API is told you are, when the cache is dropped) are the same.
 */
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
  const activeRole = useMemo(() => {
    if (personRoles.length === 0) {
      return role;
    }
    return personRoles.includes(role) ? role : highestRole(personRoles);
  }, [personRoles, role]);

  // Installed during render, not in an effect, so the first request a newly
  // mounted screen makes already carries the identity.
  setActingAs(actingAsPerson ? { id: actingAsPerson.id, roles: [activeRole] } : null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .authStatus()
      .then((status) => {
        if (!cancelled) setAuthStatus(status);
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
        if (!cancelled) setAuthLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        if (cancelled) return;
        setPeople(response.items);
        setActingAsIdState((current) => {
          if (current && response.items.some((person) => person.id === current)) {
            return current;
          }
          const next = response.items[0]?.id ?? null;
          store(USER_STORAGE_KEY, next);
          return next;
        });
      })
      .catch(() => {
        if (!cancelled) setPeople([]);
      })
      .finally(() => {
        if (!cancelled) setPeopleLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authLoading, demoMode]);

  // Every cached response belongs to whoever was acted as when it was fetched,
  // so a change of person or lens drops the cache in the handler itself.
  const dropCachedIdentity = useCallback(() => {
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo<RoleContextValue>(() => {
    const provider = authStatus?.provider ?? "dev";
    const isDevMode = provider === "dev";
    const oidcRoles = authStatus?.user?.roles.filter(isAppRole) ?? [];
    const heldRoles = isDevMode ? (personRoles.length > 0 ? personRoles : [role]) : oidcRoles;
    const selectedRole = isDevMode ? activeRole : highestRole(heldRoles);
    const authenticated = isDevMode || authStatus?.authenticated === true;
    // Capabilities follow the one role the backend is told, not every held role.
    const lens = new Set<AppRole>(isDevMode ? [selectedRole] : heldRoles);
    const admin = lens.has("admin");

    return {
      role: selectedRole,
      setRole: (nextRole) => {
        if (!isDevMode) return;
        dropCachedIdentity();
        setRoleState(nextRole);
        store(STORAGE_KEY, nextRole);
      },
      roleLabel: roleLabels[selectedRole],
      roles: heldRoles,
      provider,
      authenticated,
      authLoading,
      user: authStatus?.user ?? null,
      isDevMode,
      demoMode,
      canReadProjectProgress: admin || lens.has("po") || lens.has("mgr") || lens.has("exec"),
      canReadAggregate:
        admin || lens.has("exec") || lens.has("mgr") || lens.has("po") || lens.has("sm"),
      canManageConfig: admin,
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
      setActingAsId: (nextId) => {
        dropCachedIdentity();
        setActingAsIdState(nextId);
        store(USER_STORAGE_KEY, nextId);
        store(STORAGE_KEY, null);
        const next = people.find((person) => person.id === nextId) ?? null;
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
    demoMode,
    dropCachedIdentity,
    people,
    peopleLoading,
    personRoles,
    role,
  ]);

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}
