import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        cream: "#FFF8E7",
        sage: {
          DEFAULT: "#A8B5A0",
          light: "#C5CEBF",
          dark: "#7E8C76",
        },
        accent: {
          DEFAULT: "#C4A265",
          light: "#D4B87E",
          dark: "#A6864A",
          50: "rgba(196, 162, 101, 0.05)",
          100: "rgba(196, 162, 101, 0.10)",
          150: "rgba(196, 162, 101, 0.15)",
          200: "rgba(196, 162, 101, 0.20)",
        },
      },
      animation: {
        "wiz-glow": "wiz-glow 1.2s ease-in-out infinite",
      },
      keyframes: {
        "wiz-glow": {
          "0%, 100%": {
            filter: "drop-shadow(0 0 4px rgba(196, 162, 101, 0.6))",
          },
          "50%": {
            filter: "drop-shadow(0 0 12px rgba(196, 162, 101, 1))",
          },
        },
      },
    },
  },
  plugins: [],
};

export default config;
