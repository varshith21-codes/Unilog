import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const root = path.dirname(fileURLToPath(import.meta.url));

/**
 * Test config for the console.
 *
 * The console is ~6,700 lines of TSX that were verified only by `tsc` and the contrast script until
 * now. `tsc` proves the types line up; it cannot prove that a panel showing "no contradiction"
 * would still say so when the solver returned an error, and that class of bug is exactly where this
 * UI carries risk — several components exist specifically to keep two similar-looking states apart.
 *
 * Deliberately no coverage threshold. A number would push toward testing the easy 80% (layout,
 * pass-through props) and away from the handful of branches where being wrong misleads a reviewer
 * about their own data. The suite targets those branches by name instead.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirrors the `@/*` path mapping in tsconfig.json. Without it every component import fails.
    alias: { "@": path.resolve(root, "src") },
  },
  test: {
    environment: "jsdom",
    // Auto-unmount between tests. Without it a query can match a node left behind by a previous
    // test, which produces passes that mean nothing.
    globals: false,
    restoreMocks: true,
    include: ["src/**/*.test.{ts,tsx}"],
    // Next's build output and the Python side are not ours to run here.
    exclude: ["node_modules/**", ".next/**"],
  },
});
