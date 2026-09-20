import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef6ff",
          100: "#d8eaff",
          200: "#b6d6ff",
          300: "#86baff",
          400: "#4f95ff",
          500: "#2572f6",
          600: "#1557dc",
          700: "#1146b0",
          800: "#143d8e",
          900: "#163672",
        },
        ink: {
          50: "#f7f8fa",
          100: "#eef0f4",
          200: "#dde1e9",
          300: "#bcc3d1",
          400: "#8d96ab",
          500: "#697388",
          600: "#525a6e",
          700: "#3f4658",
          800: "#2c3242",
          900: "#1b1f2b",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "sans-serif",
        ],
      },
      boxShadow: {
        soft: "0 4px 20px -6px rgba(20, 30, 60, 0.12)",
        card: "0 1px 3px rgba(20, 30, 60, 0.06), 0 1px 2px rgba(20, 30, 60, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
