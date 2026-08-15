import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 性能优化：产物代码分割
    // - vue-vendor: vue/pinia/vue-router/axios 基础框架（长期缓存）
    // - vendor: 其余第三方依赖
    // 注意：Vite 8 (rolldown) 不支持 manualChunks 对象形式，必须用函数形式
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/[\\/]node_modules[\\/](vue|pinia|axios|vue-router)[\\/]/.test(id) ||
              /[\\/]node_modules[\\/](@vue[\\/])/.test(id)) {
            return 'vue-vendor'
          }
          return 'vendor'
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
  test: {
    environment: 'jsdom',
    globals: true
  }
})
