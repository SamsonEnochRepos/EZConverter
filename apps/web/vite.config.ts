import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `base: "./"` keeps asset URLs relative so the same build works from a web server and inside Electron.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  build: { sourcemap: true, target: "es2022" },
  server: { port: 5173 },
  test: { environment: "jsdom", include: ["src/**/*.test.ts?(x)"] },
});
