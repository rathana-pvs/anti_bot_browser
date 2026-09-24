/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#09090B', // Zinc 950
        surface: '#18181B',    // Zinc 900
        elevated: '#27272A',   // Zinc 800
        border: '#27272A',     // Zinc 800
        divider: '#3F3F46',    // Zinc 700
      },
    },
  },
  plugins: [],
}
