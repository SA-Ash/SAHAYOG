import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: { manualChunks: { graph: ["cytoscape", "cytoscape-dagre"] } },
    },
  },
  server: { proxy: { "/api": { target: "http://localhost:8000", ws: true } } },
});
