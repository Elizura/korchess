import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: "#5f6fc1",
        "accent-green": "#4ade80",
        "background-dark": "#0d1117",
        "card-dark": "#161b22",
        charcoal: "#0a0a0c",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "var(--font-space-mono)", "ui-monospace", "monospace"],
        display: ["var(--font-pixel)", "var(--font-pixel-bold)", "cursive"],
      },
      borderRadius: {
        DEFAULT: "0px",
      },
    },
  },
  plugins: [],
};

export default config;
