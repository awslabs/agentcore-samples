import { defineConfig } from "vite";

// The dev server runs inside the runtime session and is reachable only through the
// Session endpoint, which authenticates every request. Runtime forwards requests under a
// hostname of its own rather than the Session endpoint URL's, so the dev server accepts any
// Host header; its host check exists for a server exposed on a local network.
export default defineConfig({
  cacheDir: "/tmp/vite-cache",
  server: {
    host: "0.0.0.0",
    port: 8090,
    strictPort: true,
    allowedHosts: true,
  },
});
