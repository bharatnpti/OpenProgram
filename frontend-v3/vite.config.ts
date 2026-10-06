import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    // Fail loudly if 5175 is taken instead of drifting to the next free port.
    // The backend's CORS allowlist names specific ports, so a drifted server is
    // served fine but every API call is blocked -- which reads as "sign in to
    // continue" rather than as a port clash.
    strictPort: true,
  },
});
