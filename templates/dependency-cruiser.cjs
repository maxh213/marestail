module.exports = {
  forbidden: [
    { name: "no-circular", severity: "error", from: {}, to: { circular: true } },
    { name: "no-orphans", severity: "error", from: { orphan: true, pathNot: ["\\.d\\.ts$", "test-setup\\.ts$", "main\\.tsx$"] }, to: {} },
    { name: "services-stay-pure", severity: "error", from: { path: "^src/services" }, to: { path: "^src/(components|atoms|app)" } },
    { name: "atoms-do-not-render", severity: "error", from: { path: "^src/atoms" }, to: { path: "^src/components" } },
    { name: "no-react-in-services", severity: "error", from: { path: "^src/services" }, to: { path: "^node_modules/react" } },
    { name: "no-test-imports-from-prod", severity: "error", from: { pathNot: "\\.test\\.tsx?$" }, to: { path: "\\.test\\.tsx?$|test-setup\\.ts$" } },
    { name: "modules-are-entered-through-index", severity: "error", from: { path: "^src/([^/]+)/" }, to: { path: "^src/(?!$1/)[^/]+/.+", pathNot: "^src/[^/]+/index\\.tsx?$" } },
    { name: "top-level-enters-modules-through-index", severity: "error", from: { path: "^src/[^/]+\\.tsx?$" }, to: { path: "^src/[^/]+/.+", pathNot: "^src/[^/]+/index\\.tsx?$" } },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.app.json" },
    enhancedResolveOptions: { exportsFields: ["exports"], conditionNames: ["import", "require", "node", "default"] },
  },
};
