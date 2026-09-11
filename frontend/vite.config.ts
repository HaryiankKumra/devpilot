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
    watch: {
      // Inside Docker on Windows or macOS, the source is a bind mount, and
      // filesystem change events do not cross that boundary: the file inside
      // the container changes, but inotify never fires, so Vite keeps serving
      // its cached transform of the old file. Hot reload then appears to work
      // (the server is up, the page loads) while silently showing stale code
      // -- which cost a debugging session before it was noticed. Polling is
      // the documented fix. It is opt-in via CHOKIDAR_USEPOLLING so a native
      // Linux checkout, where events work, does not pay for it.
      usePolling: process.env.CHOKIDAR_USEPOLLING === 'true',
      interval: 1000,
    },
  },
});
