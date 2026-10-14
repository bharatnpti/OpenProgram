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
const PortfolioPage = page(() => import("./pages/PortfolioPage"), "PortfolioPage");
const ProjectPage = page(() => import("./pages/ProjectPage"), "ProjectPage");
const PodPage = page(() => import("./pages/PodPage"), "PodPage");
const RisksPage = page(() => import("./pages/RisksPage"), "RisksPage");
const ScopePage = page(() => import("./pages/ScopePage"), "ScopePage");
const ReportsHomePage = page(() => import("./pages/ReportsHomePage"), "ReportsHomePage");
const BriefsPage = page(() => import("./pages/BriefsPage"), "BriefsPage");
const DailyPage = page(() => import("./pages/DailyPage"), "DailyPage");
const FlowPage = page(() => import("./pages/FlowPage"), "FlowPage");
const OraPage = page(() => import("./pages/OraPage"), "OraPage");
const PeoplePage = page(() => import("./pages/PeoplePage"), "PeoplePage");
const AdminPage = page(() => import("./pages/AdminPage"), "AdminPage");

/*
 * The sidebar's pages (app/nav.ts):
 *
 *   /today                          what needs me today, per role
 *   /check-in                       my own check-in and tasks; the built-in chat on a local tenant
 *   /requests                       who is waiting on whom
 *   /portfolio                      will we make the date? (?program= picks one)
 *   /projects[/:projectId]          one project; bare opens the worst of mine
 *   /pods[/:podId]                  one pod; bare opens my own
 *   /warnings                       early warnings across projects
 *   /scope                          scope control (?project= picks one)
 *   /reports                        the day reports; /reports/briefs the briefs
 *   /reports/:projectId/daily       the end-of-day report (?report= picks one)
 *   /flow                           review flow
 *   /ora                            Ask Ora, full page
 *   /people                         people & roles
 *   /admin                          runtime configuration (admin)
 *
 * Every address from before the sidebar (/delivery/…, /signals, /coordination,
 * /chat, /reports/:id/overall, /today?pod=…) opens where it lives now, its query
 * and hash kept (app/access.ts `legacyPath`, applied by app/RouteGuard): links
 * in messages already sent must keep working. Any page takes ?asOf=YYYY-MM-DD to
 * show a past day (app/ViewingDateProvider).
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
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/projects" element={<ProjectPage />} />
        <Route path="/projects/:projectId" element={<ProjectPage />} />
        <Route path="/pods" element={<PodPage />} />
        <Route path="/pods/:podId" element={<PodPage />} />
        <Route path="/warnings" element={<RisksPage />} />
        <Route path="/scope" element={<ScopePage />} />
        <Route path="/reports" element={<ReportsHomePage />} />
        <Route path="/reports/briefs" element={<BriefsPage />} />
        <Route path="/reports/:projectId/daily" element={<DailyPage />} />
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
