import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendTarget = process.env.MANAGER_DEV_BACKEND_URL || 'http://127.0.0.1:3001';
const backendUrl = new URL(backendTarget);
if (backendUrl.protocol !== 'http:' || backendUrl.hostname !== '127.0.0.1' || backendUrl.username || backendUrl.password) {
  throw new Error('The development backend must use http://127.0.0.1.');
}
// Server-side only: never expose this token through a VITE_* variable or bundle.
const backendHeaders = process.env.MANAGER_API_TOKEN
  ? { 'X-Manager-Token': process.env.MANAGER_API_TOKEN } : undefined;

export default defineConfig({
  plugins: [react()],
  build: {
    target: 'esnext',
  },
  optimizeDeps: {
    esbuildOptions: {
      target: 'esnext',
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendTarget,
        headers: backendHeaders,
        changeOrigin: true,
        ws: true,
      },
      '/shared_media': {
        target: backendTarget,
        headers: backendHeaders,
        changeOrigin: true,
      },
    },
  },
});
