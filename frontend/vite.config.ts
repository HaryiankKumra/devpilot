import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  resolve: {
    // `@/x` beats `../../../x` for readability and survives file moves.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // Bind to all interfaces so the dev server is reachable from outside the
    // container when running under Docker Compose.
    host: true,
    port: 5173,
  },
});
