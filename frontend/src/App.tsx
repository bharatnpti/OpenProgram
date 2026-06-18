import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./app/Layout";
import { RoleProvider } from "./app/RoleContext";
import { PersonaDashboard } from "./features/personas/PersonaDashboard";
import { AdminConfigPage } from "./pages/AdminConfigPage";
import { PodDetailPage } from "./pages/PodDetailPage";
import { PodsPage } from "./pages/PodsPage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";

export function App() {
  return (
    <RoleProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/me" replace />} />
            <Route path="/me" element={<PersonaDashboard role="dev" />} />
            <Route path="/sm" element={<PersonaDashboard role="sm" />} />
            <Route path="/po" element={<PersonaDashboard role="po" />} />
            <Route path="/exec" element={<PersonaDashboard role="exec" />} />
            <Route path="/pods" element={<PodsPage />} />
            <Route path="/pods/:podId" element={<PodDetailPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
            <Route path="/portfolio" element={<PortfolioPage />} />
            <Route path="/admin" element={<AdminConfigPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </RoleProvider>
  );
}
