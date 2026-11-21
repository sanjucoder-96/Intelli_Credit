/**
 * PostCSS configuration — Tailwind CSS v4 runs as a PostCSS plugin
 * (`@tailwindcss/postcss`); there is no tailwind.config.js in v4, design
 * tokens live in src/app/globals.css via @theme (ADR-0014).
 */
const config = {
  plugins: ["@tailwindcss/postcss"],
};

export default config;
