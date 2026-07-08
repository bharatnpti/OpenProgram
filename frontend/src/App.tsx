import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
import { useRole, type AppRole } from "./app/role";
import { Button } from "./components/ui/button";
import { Skeleton } from "./components/ui/skeleton";

const PersonaDashboard = lazy(() =>
  import("./features/personas/PersonaDashboard").then((module) => ({
    default: module.PersonaDashboard,
  })),
);
const AdminConfigPage = lazy(() =>
  import("./pages/AdminConfigPage").then((module) => ({ default: module.AdminConfigPage })),
);
const MockSlackPage = lazy(() =>
  import("./pages/MockSlackPage").then((module) => ({ default: module.MockSlackPage })),
);
const PodDetailPage = lazy(() =>
  import("./pages/PodDetailPage").then((module) => ({ default: module.PodDetailPage })),
);
const PodsPage = lazy(() =>
  import("./pages/PodsPage").then((module) => ({ default: module.PodsPage })),
);
const PortfolioPage = lazy(() =>
  import("./pages/PortfolioPage").then((module) => ({ default: module.PortfolioPage })),
);
const FlowPage = lazy(() =>
  import("./pages/FlowPage").then((module) => ({ default: module.FlowPage })),
);
const RisksPage = lazy(() =>
  import("./pages/RisksPage").then((module) => ({ default: module.RisksPage })),
);
const CrossPersonRequestsPage = lazy(() =>
  import("./pages/CrossPersonRequestsPage").then((module) => ({
    default: module.CrossPersonRequestsPage,
  })),
);
const ProjectDetailPage = lazy(() =>
  import("./pages/ProjectDetailPage").then((module) => ({
    default: module.ProjectDetailPage,
  })),
);
const ProjectsPage = lazy(() =>
  import("./pages/ProjectsPage").then((module) => ({ default: module.ProjectsPage })),
);
const WorkstreamDetailPage = lazy(() =>
  import("./pages/WorkstreamDetailPage").then((module) => ({
    default: module.WorkstreamDetailPage,
  })),
);
const WorkstreamsPage = lazy(() =>
  import("./pages/WorkstreamsPage").then((module) => ({ default: module.WorkstreamsPage })),
);

const chatSimulatorFrontendEnabled =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_CHAT_SIMULATOR === "true";

export function App() {
  return (
    <RoleProvider>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/logged-out" element={<LoggedOutPage />} />
            <Route
              element={
                <RequireAuthenticated>
                  <Layout />
                </RequireAuthenticated>
              }
            >
              <Route path="/" element={<RootRedirect />} />
              <Route
                path="/me"
                element={
                  <RequireRoleAccess role="dev">
                    <PersonaDashboard role="dev" />
                  </RequireRoleAccess>
                }
              />
              <Route
                path="/sm"
                element={
                  <RequireRoleAccess role="sm">
                    <PersonaDashboard role="sm" />
                  </RequireRoleAccess>
                }
              />
              <Route
                path="/po"
                element={
                  <RequireRoleAccess role="po">
                    <PersonaDashboard role="po" />
                  </RequireRoleAccess>
                }
              />
              <Route
                path="/mgr"
                element={
                  <RequireRoleAccess role="mgr">
                    <PersonaDashboard role="mgr" />
                  </RequireRoleAccess>
                }
              />
              <Route
                path="/exec"
                element={
                  <RequireRoleAccess role="exec">
                    <PersonaDashboard role="exec" />
                  </RequireRoleAccess>
                }
              />
              <Route path="/pods" element={<PodsPage />} />
              <Route path="/pods/:podId" element={<PodDetailPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
              <Route path="/workstreams" element={<WorkstreamsPage />} />
              <Route path="/workstreams/:workstreamId" element={<WorkstreamDetailPage />} />
              <Route
                path="/flow"
                element={
                  <RequirePortfolioAccess>
                    <FlowPage />
                  </RequirePortfolioAccess>
                }
              />
              <Route
                path="/portfolio"
                element={
                  <RequirePortfolioAccess>
                    <PortfolioPage />
                  </RequirePortfolioAccess>
                }
              />
              <Route
                path="/risks"
                element={
                  <RequirePortfolioAccess>
                    <RisksPage />
                  </RequirePortfolioAccess>
                }
              />
              <Route
                path="/cross-person-requests"
                element={
                  <RequirePortfolioAccess>
                    <CrossPersonRequestsPage />
                  </RequirePortfolioAccess>
                }
              />
              <Route
                path="/admin"
                element={
                  <RequireAdminAccess>
                    <AdminConfigPage />
                  </RequireAdminAccess>
                }
              />
              <Route
                path="/mock-slack"
                element={
                  <RequireChatSimulatorAccess>
                    <MockSlackPage />
                  </RequireChatSimulatorAccess>
                }
              />
              <Route path="*" element={<RootRedirect />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </RoleProvider>
  );
}

function RequireAuthenticated({ children }: { children: ReactNode }) {
  const { authenticated, authLoading, isDevMode, signIn } = useRole();
  if (authLoading) {
    return <RouteFallback />;
  }
  if (!isDevMode && !authenticated) {
    return (
      <main className="grid min-h-screen place-items-center bg-background px-5">
        <div className="flex w-full max-w-sm flex-col gap-4 rounded-md border border-border bg-surface p-5 shadow-panel">
          <div>
            <h1 className="text-lg font-semibold">OpenProgram</h1>
            <p className="mt-1 text-sm text-muted-foreground">Sign in to continue.</p>
          </div>
          <Button type="button" onClick={signIn}>
            Sign in
          </Button>
        </div>
      </main>
    );
  }
  return children;
}

function RootRedirect() {
  const { defaultRoute } = useRole();
  return <Navigate to={defaultRoute} replace />;
}

function RequireRoleAccess({ role, children }: { role: AppRole; children: ReactNode }) {
  const { canAccessRole, defaultRoute } = useRole();
  return canAccessRole(role) ? children : <Navigate to={defaultRoute} replace />;
}

function RequireAdminAccess({ children }: { children: ReactNode }) {
  const { canAccessAdmin } = useRole();
  return canAccessAdmin ? children : <Navigate to="/me" replace />;
}

function RequirePortfolioAccess({ children }: { children: ReactNode }) {
  const { canAccessPortfolio } = useRole();
  return canAccessPortfolio ? children : <Navigate to="/me" replace />;
}

function RequireChatSimulatorAccess({ children }: { children: ReactNode }) {
  const { canAccessAdmin } = useRole();
  return canAccessAdmin && chatSimulatorFrontendEnabled ? children : <Navigate to="/me" replace />;
}

function LoggedOutPage() {
  const { signIn } = useRole();
  return (
    <main className="grid min-h-screen place-items-center bg-background px-5">
      <div className="flex w-full max-w-sm flex-col gap-4 rounded-md border border-border bg-surface p-5 shadow-panel">
        <div>
          <h1 className="text-lg font-semibold">Signed out</h1>
          <p className="mt-1 text-sm text-muted-foreground">Your OpenProgram session has ended.</p>
        </div>
        <Button type="button" onClick={signIn}>
          Sign in
        </Button>
      </div>
    </main>
  );
}

function RouteFallback() {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-3 px-5 py-5">
      <Skeleton className="h-14 w-full" />
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-80 w-full" />
    </div>
  );
}
