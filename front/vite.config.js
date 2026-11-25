import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    envDir: '.',
    envPrefix: 'VITE_',
    // Local-only proxy: production can use same-origin /api or VITE_API_BASE_URL.
    server: {
      proxy: {
        '/api': { target: env.VITE_API_BASE_URL || 'http://127.0.0.1:8010', changeOrigin: true },
      },
    },
  }
})
