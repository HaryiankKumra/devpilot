/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Semantic aliases for review severity, so the meaning lives in one
        // place rather than being re-picked at every call site.
        severity: {
          critical: '#b91c1c',
          high: '#c2410c',
          medium: '#a16207',
          low: '#0369a1',
        },
      },
    },
  },
  plugins: [],
};
