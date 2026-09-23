import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'https://il9nu3s9c3.execute-api.us-east-1.amazonaws.com/prod',
        changeOrigin: true,
        secure: true,
      },
    },
  },
})
