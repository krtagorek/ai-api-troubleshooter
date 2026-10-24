import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { port: 5173, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
  preview: { port: 4173 },
  build: { rollupOptions: { output: { manualChunks: { react: ['react', 'react-dom', 'react-router-dom'], data: ['@tanstack/react-query', 'zod'] } } } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], include: ['src/**/*.test.{ts,tsx}'], restoreMocks: true },
})
