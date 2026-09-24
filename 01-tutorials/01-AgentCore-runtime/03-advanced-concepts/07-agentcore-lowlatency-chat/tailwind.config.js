/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        "primary-teal": "#2B5F5F",
        "primary-coral": "#FF6B4A",
        "primary-orange": "#FF9B4A",
        "primary-yellow": "#FFC94A",
      },
    },
  },
  plugins: [],
};
