/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        studio: {
          dark: '#090d16',
          panel: '#0f172a',
          surface: '#182235',
          border: '#1e293b',
          light: '#f8fafc',
          lightPanel: '#ffffff',
          lightSurface: '#f1f5f9',
          lightBorder: '#e2e8f0',
          accent: '#0284c7',
          orange: '#ea580c',
          emerald: '#10b981',
          purple: '#8b5cf6',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      fontSize: {
        '2xs': '0.65rem',
      },
    },
  },
  plugins: [],
}
