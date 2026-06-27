import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
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
              <Route path="/portfolio" element={<PortfolioPage />} />
              <Route path="/admin" element={<AdminConfigPage />} />
              <Route path="/mock-slack" element={<MockSlackPage />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </RoleProvider>
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
