import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { apiClient, apiUrl, setActingAs } from "../api/client";
import type { AuthStatusResponse, DevUserResponse } from "../api/schema";
import { describeAuthFailure } from "./authWords";
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
  type AuthProblem,
  type RoleContextValue,
} from "./role";

const AUTH_STATUS_PATH = "/api/v1/auth/status";

/*
 * Ported from frontend-v2's RoleProvider. Keep the two in step: the identity
 * rules (who the API is told you are, when the cache is dropped) are the same.
 */
export function RoleProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [role, setRoleState] = useState<AppRole>(readStoredRole);
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authProblem, setAuthProblem] = useState<AuthProblem | null>(null);
  const [authAttempt, setAuthAttempt] = useState(0);
  const [authRetrying, setAuthRetrying] = useState(false);
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

  // A failed answer is not "signed out": guessing a provider here used to send
  // people to a Sign in button that could never work while the backend was
  // down. The problem gets its own screen, with the address tried and a retry.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .authStatus()
      .then((status) => {
        if (cancelled) return;
        // After a retry the roster has to load again before any screen asks
        // for anything, as on a first load.
        if (status.demo_mode) setPeopleLoading(true);
        setAuthStatus(status);
        setAuthProblem(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setAuthStatus(null);
        setAuthProblem({ url: apiUrl(AUTH_STATUS_PATH), detail: describeAuthFailure(error) });
      })
      .finally(() => {
        if (cancelled) return;
        setAuthLoading(false);
        setAuthRetrying(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authAttempt]);

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
      authProblem,
      retryAuth: () => {
        setAuthRetrying(true);
        setAuthAttempt((attempt) => attempt + 1);
      },
      authRetrying,
      user: authStatus?.user ?? null,
      isDevMode,
      demoMode,
      canReadProjectProgress: admin || lens.has("po") || lens.has("mgr") || lens.has("exec"),
      canReadAggregate:
        admin || lens.has("exec") || lens.has("mgr") || lens.has("po") || lens.has("sm"),
      canManageConfig: admin,
      canReadPodDetail: admin || lens.has("sm") || lens.has("mgr"),
      canReadPortfolio: admin || lens.has("mgr") || lens.has("exec"),
      canSetUpDayReports: admin || lens.has("sm") || lens.has("mgr"),
      chatEnabled: authStatus?.chat_enabled === true,
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
    authProblem,
    authRetrying,
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
