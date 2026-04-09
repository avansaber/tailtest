// Minimal vitest config. JSRunner's discover() looks for this file
// (or vitest in package.json devDeps) to decide to use vitest.

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.js"],
  },
});
