import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
import { useRole } from "./app/role";
import { Pill } from "./components/ui/Pill";

const ProjectsPage = lazy(() =>
  import("./pages/ProjectsPage").then((module) => ({ default: module.ProjectsPage })),
);
const DailyPage = lazy(() =>
  import("./pages/DailyPage").then((module) => ({ default: module.DailyPage })),
);
const OverallPage = lazy(() =>
  import("./pages/OverallPage").then((module) => ({ default: module.OverallPage })),
);

/*
 * Routes are project-first: every report view belongs to one project, and the
 * project is in the URL so any view can be linked.
 *
 *   /                              pick a project
 *   /projects/:projectId/daily     the end-of-day report (?report= picks one of several)
 *   /projects/:projectId/overall   the project's state since it started
 */
export function App() {
  return (
    <RoleProvider>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/logged-out" element={<SignInScreen title="Signed out" />} />
            <Route
              element={
                <RequireAuthenticated>
                  <Layout />
                </RequireAuthenticated>
              }
            >
              <Route path="/" element={<ProjectsPage />} />
              <Route path="/projects/:projectId" element={<ProjectRedirect />} />
              <Route path="/projects/:projectId/daily" element={<DailyPage />} />
              <Route path="/projects/:projectId/overall" element={<OverallPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </RoleProvider>
  );
}

function ProjectRedirect() {
  const { projectId } = useParams();
  return <Navigate to={`/projects/${projectId}/daily`} replace />;
}

function RequireAuthenticated({ children }: { children: ReactNode }) {
  const { authenticated, authLoading, isDevMode, peopleLoading } = useRole();
  // In dev mode the acting-as person decides the role set, so wait for the
  // roster rather than briefly deciding access from a stale role.
  if (authLoading || (isDevMode && peopleLoading)) {
    return <RouteFallback />;
  }
  if (!isDevMode && !authenticated) {
    return <SignInScreen title="OpenProgram Reports" />;
  }
  return children;
}

function SignInScreen({ title }: { title: string }) {
  const { signIn } = useRole();
  return (
    <main className="grid min-h-screen place-items-center bg-white px-4">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-[28px] font-extrabold">{title}</h1>
          <p className="mt-1 text-grey-secondary">Sign in to read your projects' reports.</p>
        </div>
        <Pill onClick={signIn}>Sign in</Pill>
      </div>
    </main>
  );
}

function RouteFallback() {
  return <div className="p-8 text-grey-secondary">Loading…</div>;
}
