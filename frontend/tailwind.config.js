/** @type {import('tailwindcss').Config} */
// Control-room palette: graphite surfaces, one neutral text scale, colour only for meaning
// (severity, status, connection). No gradients, minimal rounding.
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#0a0c0f', 900: '#0f1216', 850: '#13171c', 800: '#181d23', 700: '#222831', 600: '#2d3540', 500: '#3b4552' },
        fg: { DEFAULT: '#d6dbe1', muted: '#8d96a2', faint: '#5f6873' },
        sev: { critical: '#ff4d4f', high: '#f2762e', medium: '#d9a514', low: '#7d8b99' },
        st: { open: '#e6e9ed', inv: '#4c9fe8', ok: '#3fae74', fp: '#8d96a2' },
        sig: { live: '#3fae74', warn: '#d9a514', down: '#ff4d4f' },
        focus: '#7cc4ff',
      },
      fontFamily: {
        sans: ['"Segoe UI"', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"Cascadia Mono"', '"JetBrains Mono"', 'Consolas', 'ui-monospace', 'monospace'],
      },
      fontSize: { '2xs': ['10.5px', '14px'] },
      borderRadius: { DEFAULT: '2px', sm: '2px', md: '3px' },
    },
  },
  plugins: [],
};
