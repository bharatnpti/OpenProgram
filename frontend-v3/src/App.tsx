import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
import { ViewingDateProvider } from "./app/ViewingDateProvider";
import { useRole } from "./app/role";
import { Pill } from "./components/ui/Pill";
import { AssistantProvider } from "./features/assistant/AssistantProvider";

const page = <K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) =>
  lazy(() => load().then((module) => ({ default: module[name] })));

const TodayPage = page(() => import("./pages/TodayPage"), "TodayPage");
const DeliveryPage = page(() => import("./pages/DeliveryPage"), "DeliveryPage");
const SignalsPage = page(() => import("./pages/SignalsPage"), "SignalsPage");
const CoordinationPage = page(() => import("./pages/CoordinationPage"), "CoordinationPage");
const ReportsHomePage = page(() => import("./pages/ReportsHomePage"), "ReportsHomePage");
const DailyPage = page(() => import("./pages/DailyPage"), "DailyPage");
const OverallPage = page(() => import("./pages/OverallPage"), "OverallPage");
const ChatPage = page(() => import("./pages/ChatPage"), "ChatPage");
const AdminPage = page(() => import("./pages/AdminPage"), "AdminPage");
const OraPage = page(() => import("./pages/OraPage"), "OraPage");

/*
 *   /today                          the role's own home (developer, scrum master,
 *                                   product owner, or the portfolio view)
 *   /delivery[/:kind/:id]           walk the graph: program, project, workstream, pod
 *   /signals                        flow through review; risks across projects (portfolio roles)
 *   /coordination                   requests between people, briefs
 *   /reports                        every project's reports
 *   /reports/:projectId/daily       the end-of-day report (?report= picks one)
 *   /reports/:projectId/overall     the project's state since it started
 *   /chat                           the built-in chat (local tenants only)
 *   /admin                          runtime configuration (admin)
 *
 * Any of them takes ?asOf=YYYY-MM-DD to show a past day (app/ViewingDateProvider).
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
        <Route path="/delivery" element={<DeliveryPage />} />
        <Route path="/delivery/:kind/:id" element={<DeliveryPage />} />
        <Route path="/signals" element={<SignalsPage />} />
        <Route path="/coordination" element={<CoordinationPage />} />
        <Route path="/reports" element={<ReportsHomePage />} />
        <Route path="/reports/:projectId" element={<ProjectRedirect />} />
        <Route path="/reports/:projectId/daily" element={<DailyPage />} />
        <Route path="/reports/:projectId/overall" element={<OverallPage />} />
        {/* Links from the first scaffold keep working. */}
        <Route path="/projects/:projectId" element={<ProjectRedirect />} />
        <Route path="/projects/:projectId/:view" element={<ProjectRedirect />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/ora" element={<OraPage />} />
        <Route path="*" element={<Navigate to="/today" replace />} />
      </Route>
    </Routes>
  );
}

function ProjectRedirect() {
  const { projectId, view } = useParams();
  return (
    <Navigate to={`/reports/${projectId}/${view === "overall" ? "overall" : "daily"}`} replace />
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
