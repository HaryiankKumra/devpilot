/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // Inter for text, JetBrains Mono for anything that came from a
        // machine: SHAs, file paths, line numbers, the score itself.
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: [
          '"JetBrains Mono"',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'monospace',
        ],
      },
      colors: {
        // One dark palette, named by role rather than by shade, so a page
        // never has to know a hex value and the whole theme lives here.
        ink: '#0a0c11', // page background
        surface: '#11141b', // cards
        raised: '#181c26', // hover, inputs, chips
        line: '#232937', // borders
        fg: '#e8ebf1', // primary text
        muted: '#8f98aa', // secondary text
        dim: '#5b6478', // tertiary text, placeholders
        // A single signal colour. Amber reads "instrument", not "AI".
        accent: {
          DEFAULT: '#f0b445',
          hover: '#f6c76a',
          ink: '#1a1200', // text on an accent background
          dim: 'rgba(240, 180, 69, 0.14)',
        },
        // Review severity, matching the backend's enum. Meaning lives here.
        severity: {
          critical: '#f0625d',
          high: '#f0954a',
          medium: '#e3b34a',
          low: '#5aa9e6',
        },
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(240, 180, 69, 0.35), 0 0 24px rgba(240, 180, 69, 0.12)',
        card: '0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 30px rgba(0,0,0,0.35)',
      },
      keyframes: {
        rise: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        sweep: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
      },
      animation: {
        rise: 'rise 600ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
        sweep: 'sweep 2.4s linear infinite',
        blink: 'blink 1s steps(2, start) infinite',
      },
    },
  },
  plugins: [],
};
