import { defineConfig } from "vite";

export default defineConfig({
  optimizeDeps: {
    include: ["protobufjs/minimal"],
  },
  server: {
    proxy: {
      // Forward /start to the Pipecat server so the client
      // can fetch the WebSocket URL without CORS issues.
      "/start": {
        target: "http://localhost:8081",
        changeOrigin: true,
      },
    },
  },
});
