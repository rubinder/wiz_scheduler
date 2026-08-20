import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-display)"],
        body: ["var(--font-body)"],
        data: ["var(--font-data)"],
      },
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
        ink: "#1A1815",
        newsprint: "#E9E4D8",
        paper: "#F5F2EA",
        rule: "#C9C0B0",
        marker: "#FF5A1F",
        clear: "#1D7357",
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
