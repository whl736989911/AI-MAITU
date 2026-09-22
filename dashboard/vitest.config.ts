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

// Cap workers at the physical core count. jsdom tests here are dominated by
// *synchronous* `getComputedStyle` work, so once the worker count exceeds the
// physical cores the OS timeslices that work and stretches it 5-6x — a ~1 s
// test turns into 5 s+ and trips the 5000 ms default timeout. Halving the
// logical CPU count approximates physical cores on SMT machines and is merely
// more conservative elsewhere; do not raise this back to the Vitest default.
const maxWorkers = Math.max(1, Math.floor(os.cpus().length / 2));

export default defineConfig({
  plugins: [react()],
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
