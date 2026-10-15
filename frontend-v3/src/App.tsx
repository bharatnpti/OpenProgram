import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
import { ViewingDateProvider } from "./app/ViewingDateProvider";
import { useRole } from "./app/role";
import { Pill } from "./components/ui/Pill";
import { AssistantProvider } from "./features/assistant/AssistantProvider";

const page = <K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) =>
  lazy(() => load().then((module) => ({ default: module[name] })));

const TodayPage = page(() => import("./pages/TodayPage"), "TodayPage");
const CheckinPage = page(() => import("./pages/CheckinPage"), "CheckinPage");
const RequestsPage = page(() => import("./pages/RequestsPage"), "RequestsPage");
const ProjectsLanding = page(() => import("./pages/HubPage"), "ProjectsLanding");
const HubPage = lazy(() =>
  import("./pages/HubPage").then((module) => ({ default: module.HubPage })),
);
const FlowPage = page(() => import("./pages/FlowPage"), "FlowPage");
const OraPage = page(() => import("./pages/OraPage"), "OraPage");
const PeoplePage = page(() => import("./pages/PeoplePage"), "PeoplePage");
const AdminPage = page(() => import("./pages/AdminPage"), "AdminPage");

/*
 * The sidebar's pages (app/nav.ts):
 *
 *   /today                                    what needs me today, per role
 *   /check-in                                 my own check-in and tasks
 *   /requests                                 who is waiting on whom
 *   /projects                                 the Projects hub; opens by role
 *     /portfolio/:tab                         All projects
 *     /projects/:projectId/:tab               one project
 *     /projects/:projectId/pods/:podId/:tab   one pod, its part of that project
 *                                             (tabs: date, warnings, scope, reports)
 *   /flow                                     review flow
 *   /ora                                      Ask Ora, full page
 *   /people                                   people & roles
 *   /admin                                    runtime configuration (admin)
 *
 * Every address from before the hub (/pods/…, /warnings, /scope, /reports/…,
 * /delivery/…, /signals, /coordination, /chat, /today?pod=…) opens where it
 * lives now, its query and hash kept (app/access.ts `legacyPath`, applied by
 * app/RouteGuard): links in messages already sent must keep working. Any page
 * takes ?asOf=YYYY-MM-DD to show a past day (app/ViewingDateProvider).
 */
export function App() {
  return (
    <RoleProvider>
      <BrowserRouter>
        <ViewingDateProvider>
          <Suspense fallback={<RouteFallback />}>
            <AppRoutes />
          </Suspense>
        </ViewingDateProvider>
      </BrowserRouter>
    </RoleProvider>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/logged-out" element={<SignInScreen title="Signed out" />} />
      <Route
        element={
          <RequireAuthenticated>
            {/* The assistant's conversation lives above the tabs, so it stays between them. */}
            <AssistantProvider>
              <Layout />
            </AssistantProvider>
          </RequireAuthenticated>
        }
      >
        <Route path="/" element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<TodayPage />} />
        <Route path="/check-in" element={<CheckinPage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/projects" element={<ProjectsLanding />} />
        <Route path="/portfolio/:tab?" element={<HubPage level="all" />} />
        <Route path="/projects/:projectId/:tab?" element={<HubPage level="project" />} />
        <Route path="/projects/:projectId/pods/:podId/:tab?" element={<HubPage level="pod" />} />
        <Route path="/flow" element={<FlowPage />} />
        <Route path="/ora" element={<OraPage />} />
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/admin" element={<AdminPage />} />
        {/* An old address is placed by the guard before this is reached. */}
        <Route path="*" element={<Navigate to="/today" replace />} />
      </Route>
    </Routes>
  );
}

function RequireAuthenticated({ children }: { children: ReactNode }) {
  const { authenticated, authLoading, authProblem, isDevMode, peopleLoading } = useRole();
  if (authLoading) {
    return <RouteFallback />;
  }
  // Checked before the provider: with no answer there is no provider to go by.
  if (authProblem) {
    return <BackendUnreachable />;
  }
  // In dev mode the acting-as person decides the role set, so wait for the
  // roster rather than briefly deciding access from a stale role.
  if (isDevMode && peopleLoading) {
    return <RouteFallback />;
  }
  if (!isDevMode && !authenticated) {
    return <SignInScreen title="OpenProgram" />;
  }
  return children;
}

/**
 * The backend did not say who this is. Signing in can't help until it
 * answers, so this says which address was tried and offers to try again.
 */
function BackendUnreachable() {
  const { authProblem, retryAuth, authRetrying } = useRole();
  return (
    <main className="grid min-h-screen place-items-center bg-white px-4">
      <div className="flex max-w-[560px] flex-col gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold tracking-tight text-balance">
            Can't reach the OpenProgram backend
          </h1>
          <p className="mt-2 text-[15px] text-grey-body">
            The console asked who you are at{" "}
            <code className="break-all rounded-md bg-grey-fill px-1.5 py-0.5 text-[13px] text-ink">
              {authProblem?.url}
            </code>
            . {authProblem?.detail}
          </p>
          <p className="mt-2 text-[13px] text-grey-secondary">
            On a local setup, start the backend, or point this console at it with VITE_API_BASE_URL.
            Nothing was changed.
          </p>
        </div>
        <div>
          <Pill onClick={retryAuth} disabled={authRetrying} size="md">
            {authRetrying ? "Trying again…" : "Try again"}
          </Pill>
        </div>
      </div>
    </main>
  );
}

function SignInScreen({ title }: { title: string }) {
  const { signIn } = useRole();
  return (
    <main className="grid min-h-screen place-items-center bg-white px-4">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold">{title}</h1>
          <p className="mt-1 text-grey-secondary">Sign in to continue.</p>
        </div>
        <Pill onClick={signIn}>Sign in</Pill>
      </div>
    </main>
  );
}

function RouteFallback() {
  return <div className="p-8 text-grey-secondary">Loading…</div>;
}
