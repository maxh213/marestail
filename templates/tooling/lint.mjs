import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["node_modules/**", "dist/**", "coverage/**"] },
  { files: @SOURCES@, extends: [tseslint.configs.recommended] },
);
