import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/replays': 'http://127.0.0.1:8000', '/planning': 'http://127.0.0.1:8000', '/validate': 'http://127.0.0.1:8000', '/approvals': 'http://127.0.0.1:8000', '/certificates': 'http://127.0.0.1:8000' } },
  build: { outDir: 'dist', emptyOutDir: true },
});