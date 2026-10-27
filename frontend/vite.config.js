import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development (npm run dev) the page runs on :5173 and the API on :8000.
// The proxy sends every /api/... request to the API, so the code can always
// call "/api/..." - the same path nginx serves in Docker and Kubernetes.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
