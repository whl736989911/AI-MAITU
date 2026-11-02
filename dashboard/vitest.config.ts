/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";
import os from "os";

/**
 * Standalone Vitest config — kept separate from ``vite.config.ts`` so:
 * 1. The PWA plugin and SPA build tweaks don't run for unit tests
 *    (they pull heavy deps and slow startup by ~3 s).
 * 2. We can keep ``test.environment`` / ``setupFiles`` in one obvious
 *    place without polluting the prod build config.
 */

// Synchronous jsdom/Antd CSS work becomes costly under CPU and memory contention.
// Bound workers without weakening assertions or relaxing test deadlines.
const maxWorkers = Math.max(1, Math.min(2, Math.floor(os.cpus().length / 2)));

export default defineConfig({
  plugins: [react()],
  define: { BASE_URL: JSON.stringify("") },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // jsdom + antd render costs seconds per suite on Windows, and the same test
    // that takes ~2.9 s alone exceeds the 5 s default while two workers share
    // the CPU with collect/transform work. A wall-clock race there fails
    // unrelated suites at random, so the budget is raised for real work while a
    // genuine hang still fails at 20 s.
    testTimeout: 20_000,
    hookTimeout: 20_000,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    pool: "threads",
    maxWorkers,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/pages/Agent/Memory/**"],
    },
  },
});
