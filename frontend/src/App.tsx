import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { PersonaDashboard } from "./features/personas/PersonaDashboard";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/me" replace />} />
        <Route path="/me" element={<PersonaDashboard role="dev" />} />
        <Route path="/sm" element={<PersonaDashboard role="sm" />} />
        <Route path="/po" element={<PersonaDashboard role="po" />} />
        <Route path="/exec" element={<PersonaDashboard role="exec" />} />
      </Routes>
    </BrowserRouter>
  );
}
