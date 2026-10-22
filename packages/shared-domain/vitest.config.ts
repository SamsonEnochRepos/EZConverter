import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { globalSetup: ["./test/global-setup.ts"], testTimeout: 120_000, hookTimeout: 60_000, include: ["test/**/*.test.ts"] },
});
