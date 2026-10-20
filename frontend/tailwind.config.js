/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  // Same convention as BPM Tagger: the look lives in src/styles/design-system.css (tokens +
  // component classes); Tailwind is layered on for incidental layout utilities only, so
  // preflight stays off to avoid fighting the reset.
  corePlugins: { preflight: false, container: false },
  plugins: [],
};
