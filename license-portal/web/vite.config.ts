import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The console talks to the Python portal on 127.0.0.1:8091 during development.
// In production Nginx serves `dist/` and proxies `/api` to the same backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8091',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1400,
  },
})
