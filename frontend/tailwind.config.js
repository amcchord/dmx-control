/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0b0d12",
          elev: "#141821",
          card: "#1a1f2b",
        },
        line: "#242a38",
        accent: {
          DEFAULT: "#7c4dff",
          hover: "#9670ff",
        },
        muted: "#8791a7",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.4), 0 8px 24px -12px rgba(124,77,255,0.15)",
      },
    },
  },
  plugins: [],
};
