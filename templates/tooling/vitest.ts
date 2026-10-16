import { defineConfig } from "vitest/config";

export default defineConfig({
  root: process.cwd(),
  test: {
    globals: true,
    include: @TESTS@,
    coverage: { provider: "v8", include: @SOURCES@, exclude: @TESTS@ },
  },
});
