import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
import { useRole } from "./app/role";
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
const ProjectDetailPage = lazy(() =>
  import("./pages/ProjectDetailPage").then((module) => ({
    default: module.ProjectDetailPage,
  })),
);
const ProjectsPage = lazy(() =>
  import("./pages/ProjectsPage").then((module) => ({ default: module.ProjectsPage })),
);

const chatSimulatorFrontendEnabled =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_CHAT_SIMULATOR === "true";

export function App() {
  return (
    <RoleProvider>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/me" replace />} />
              <Route path="/me" element={<PersonaDashboard role="dev" />} />
              <Route path="/sm" element={<PersonaDashboard role="sm" />} />
              <Route path="/po" element={<PersonaDashboard role="po" />} />
              <Route path="/mgr" element={<PersonaDashboard role="mgr" />} />
              <Route path="/exec" element={<PersonaDashboard role="exec" />} />
              <Route path="/pods" element={<PodsPage />} />
              <Route path="/pods/:podId" element={<PodDetailPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
              <Route
                path="/portfolio"
                element={
                  <RequirePortfolioAccess>
                    <PortfolioPage />
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
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </RoleProvider>
  );
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
  return canAccessAdmin && chatSimulatorFrontendEnabled ? (
    children
  ) : (
    <Navigate to="/me" replace />
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
