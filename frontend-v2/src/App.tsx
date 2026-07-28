import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleProvider";
import { useRole } from "./app/role";

const TodayPage = lazy(() =>
  import("./pages/TodayPage").then((module) => ({ default: module.TodayPage })),
);
const DeliveryPage = lazy(() =>
  import("./pages/DeliveryPage").then((module) => ({ default: module.DeliveryPage })),
);
const SignalsPage = lazy(() =>
  import("./pages/SignalsPage").then((module) => ({ default: module.SignalsPage })),
);
const CoordinationPage = lazy(() =>
  import("./pages/CoordinationPage").then((module) => ({ default: module.CoordinationPage })),
);
const AdminPage = lazy(() =>
  import("./pages/AdminPage").then((module) => ({ default: module.AdminPage })),
);
const SimPage = lazy(() =>
  import("./pages/SimPage").then((module) => ({ default: module.SimPage })),
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
              <Route path="/today" element={<TodayPage />} />
              <Route path="/delivery" element={<DeliveryPage />} />
              <Route path="/delivery/:kind/:id" element={<DeliveryPage />} />
              <Route path="/signals" element={<SignalsPage />} />
              <Route path="/coordination" element={<CoordinationPage />} />
              <Route
                path="/admin"
                element={
                  <RequireAdminAccess>
                    <AdminPage />
                  </RequireAdminAccess>
                }
              />
              <Route
                path="/sim"
                element={
                  <RequireChatSimulatorAccess>
                    <SimPage />
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
      <main style={{ display: "grid", minHeight: "100vh", placeItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <h1>OpenProgram</h1>
            <p>Sign in to continue.</p>
          </div>
          <button type="button" onClick={signIn}>
            Sign in
          </button>
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

function RequireAdminAccess({ children }: { children: ReactNode }) {
  const { canAccessAdmin } = useRole();
  return canAccessAdmin ? children : <Navigate to="/today" replace />;
}

function RequireChatSimulatorAccess({ children }: { children: ReactNode }) {
  const { canAccessAdmin } = useRole();
  return canAccessAdmin && chatSimulatorFrontendEnabled ? (
    children
  ) : (
    <Navigate to="/today" replace />
  );
}

function LoggedOutPage() {
  const { signIn } = useRole();
  return (
    <main style={{ display: "grid", minHeight: "100vh", placeItems: "center" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <h1>Signed out</h1>
          <p>Your OpenProgram session has ended.</p>
        </div>
        <button type="button" onClick={signIn}>
          Sign in
        </button>
      </div>
    </main>
  );
}

function RouteFallback() {
  return <div style={{ padding: 32 }}>Loading…</div>;
}
