import js from "@eslint/js";
import nextPlugin from "eslint-config-next";
import globals from "globals";

const nextRules = Array.isArray(nextPlugin) ? nextPlugin : [nextPlugin];

const eslintConfig = [
  js.configs.recommended,
  ...nextRules,
  {
    files: ["**/*.{js,mjs,cjs,ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "@next/next/no-html-link-for-pages": "off",
    },
  },
  {
    files: ["**/*.{ts,tsx}"],
    rules: {
      "no-undef": "off",
      "no-unused-vars": "off",
      "react-hooks/incompatible-library": "off",
    },
  },
  {
    ignores: [".next/**", "node_modules/**"],
  },
];

export default eslintConfig;
