/** @type {import('tailwindcss').Config} */
// Tailwind + DaisyUI config for the htmx web frontend.
//
// The CSS is compiled ahead of time, never by a runtime CDN JIT: identical
// visual result, zero runtime compile cost, and classes the `content`
// scanner cannot see fail loudly in dev instead of silently in prod. If you
// put Tailwind classes anywhere outside the globs below (a Python string, a
// new template directory), add that path here or they will not be emitted.
module.exports = {
  content: [
    "./app/components/web_frontend/templates/**/*.html",
    "./app/components/web_frontend/static/js/**/*.js",
  ],
  theme: {
    extend: {
      // ---------------------------------------------------------------
      // BRAND COLORS — the single rebrand point.
      // Change these (and the DaisyUI theme below, which mirrors them)
      // and the whole web frontend follows. Nothing else hardcodes a hex.
      // ---------------------------------------------------------------
      colors: {
        aegis: {
          bg: "#090B0D", // Page background
          card: "#111418", // Card/surface background
          border: "#272C36", // Borders, dividers
          text: "#EEF1F4", // Primary text
          muted: "#7E8A9A", // Secondary/muted text
          teal: "#17CCBF", // Brand accent
          amber: "#F59E0B", // Warning/highlight
        },
      },
      fontSize: {
        xxs: ["10px", "14px"],
      },
    },
  },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      {
        // Mirrors the brand block above in DaisyUI's semantic slots, so
        // component classes (btn, card, alert) match the hand-written
        // aegis-* utilities.
        aegis: {
          primary: "#17CCBF", // Teal — brand accent
          secondary: "#3B82F6", // Blue
          accent: "#F59E0B", // Amber
          neutral: "#111418", // Card background
          "base-100": "#090B0D", // Page background
          "base-200": "#0E1014", // Slightly elevated
          "base-300": "#111418", // Card background
          "base-content": "#EEF1F4", // Default text
          info: "#06B6D4",
          success: "#17CCBF", // Unified with the brand accent
          warning: "#F59E0B",
          error: "#EF4444",
        },
      },
    ],
  },
};
