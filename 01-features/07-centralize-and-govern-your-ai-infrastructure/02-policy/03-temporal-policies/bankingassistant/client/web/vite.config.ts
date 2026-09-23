import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on 5173; proxy API calls to the Express backend on 8787.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8787",
    },
  },
});
