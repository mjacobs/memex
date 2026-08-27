import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../memex/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8780",
      "/internal": "http://localhost:8780",
    },
  },
});
