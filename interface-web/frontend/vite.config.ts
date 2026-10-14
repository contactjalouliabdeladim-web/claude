import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Cible large : le Mac peut être sous High Sierra (Safari 13, Chrome 116, Firefox ESR 115).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/atlas_server/static",
    emptyOutDir: true,
    target: ["es2017", "safari12", "chrome64", "firefox68", "edge79"],
    cssTarget: ["safari12", "chrome64", "firefox68"],
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8765", ws: true, changeOrigin: false },
    },
  },
});
