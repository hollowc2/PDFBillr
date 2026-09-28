/** Tailwind v3 config. Rebuild with `make css`; CI fails if static/css/app.css is stale. */
const token = (name) => `rgb(var(--color-${name}) / <alpha-value>)`;

module.exports = {
  content: ["templates/**/*.html", "static/js/**/*.js"],
  darkMode: "class",
  theme: {
    extend: {
      // "Ledger" design tokens. Values live in static/css/src.css as CSS
      // variables so the .dark class swaps them without dark: variants.
      colors: {
        paper: token("paper"),
        surface: token("surface"),
        rule: { DEFAULT: token("rule"), strong: token("rule-strong") },
        ink: token("ink"),
        graphite: token("graphite"),
        faint: token("faint"),
        accent: {
          DEFAULT: token("accent"),
          hover: token("accent-hover"),
          tint: token("accent-tint"),
          on: token("on-accent"),
        },
        paid: { DEFAULT: token("paid"), tint: token("paid-tint") },
        due: { DEFAULT: token("due"), tint: token("due-tint") },
        overdue: { DEFAULT: token("overdue"), tint: token("overdue-tint") },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        display: ["Schibsted Grotesk", "Helvetica Neue", "Arial", "sans-serif"],
      },
    },
  },
  plugins: [],
};
