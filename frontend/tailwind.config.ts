import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      animation: {
        "wiz-glow": "wiz-glow 1.2s ease-in-out infinite",
      },
      keyframes: {
        "wiz-glow": {
          "0%, 100%": {
            filter: "drop-shadow(0 0 4px rgba(165, 120, 255, 0.6))",
          },
          "50%": {
            filter: "drop-shadow(0 0 12px rgba(165, 120, 255, 1))",
          },
        },
      },
    },
  },
  plugins: [],
};

export default config;
