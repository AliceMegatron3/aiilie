// vite.config.js
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "file:///C:/Users/11482/Documents/aiilie/frontend/node_modules/vite/dist/node/index.js";
import vue from "file:///C:/Users/11482/Documents/aiilie/frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs";
var __vite_injected_original_import_meta_url = "file:///C:/Users/11482/Documents/aiilie/frontend/vite.config.js";
var vite_config_default = defineConfig({
  base: "./",
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", __vite_injected_original_import_meta_url))
    }
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // 性能优化：产物代码分割
    // - vue-vendor: vue/pinia/vue-router/axios 基础框架（长期缓存）
    // - vendor: 其余第三方依赖
    // 注意：Vite 8 (rolldown) 不支持 manualChunks 对象形式，必须用函数形式
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return void 0;
          if (/[\\/]node_modules[\\/](vue|pinia|axios|vue-router)[\\/]/.test(id) || /[\\/]node_modules[\\/](@vue[\\/])/.test(id)) {
            return "vue-vendor";
          }
          return "vendor";
        }
      }
    },
    chunkSizeWarningLimit: 700
  },
  test: {
    environment: "jsdom",
    globals: true
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcuanMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCJDOlxcXFxVc2Vyc1xcXFwxMTQ4MlxcXFxEb2N1bWVudHNcXFxcYWlpbGllXFxcXGZyb250ZW5kXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ZpbGVuYW1lID0gXCJDOlxcXFxVc2Vyc1xcXFwxMTQ4MlxcXFxEb2N1bWVudHNcXFxcYWlpbGllXFxcXGZyb250ZW5kXFxcXHZpdGUuY29uZmlnLmpzXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ltcG9ydF9tZXRhX3VybCA9IFwiZmlsZTovLy9DOi9Vc2Vycy8xMTQ4Mi9Eb2N1bWVudHMvYWlpbGllL2Zyb250ZW5kL3ZpdGUuY29uZmlnLmpzXCI7aW1wb3J0IHsgZmlsZVVSTFRvUGF0aCwgVVJMIH0gZnJvbSAnbm9kZTp1cmwnXG5pbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tICd2aXRlJ1xuaW1wb3J0IHZ1ZSBmcm9tICdAdml0ZWpzL3BsdWdpbi12dWUnXG5cbi8vIGh0dHBzOi8vdml0ZWpzLmRldi9jb25maWcvXG5leHBvcnQgZGVmYXVsdCBkZWZpbmVDb25maWcoe1xuICBiYXNlOiAnLi8nLFxuICBwbHVnaW5zOiBbdnVlKCldLFxuICByZXNvbHZlOiB7XG4gICAgYWxpYXM6IHtcbiAgICAgICdAJzogZmlsZVVSTFRvUGF0aChuZXcgVVJMKCcuL3NyYycsIGltcG9ydC5tZXRhLnVybCkpXG4gICAgfVxuICB9LFxuICBzZXJ2ZXI6IHtcbiAgICBwcm94eToge1xuICAgICAgJy9hcGknOiB7XG4gICAgICAgIHRhcmdldDogJ2h0dHA6Ly8xMjcuMC4wLjE6ODAwMCcsXG4gICAgICAgIGNoYW5nZU9yaWdpbjogdHJ1ZVxuICAgICAgfVxuICAgIH1cbiAgfSxcbiAgYnVpbGQ6IHtcbiAgICBvdXREaXI6ICdkaXN0JyxcbiAgICBlbXB0eU91dERpcjogdHJ1ZSxcbiAgICAvLyBcdTYwMjdcdTgwRkRcdTRGMThcdTUzMTZcdUZGMUFcdTRFQTdcdTcyNjlcdTRFRTNcdTc4MDFcdTUyMDZcdTUyNzJcbiAgICAvLyAtIHZ1ZS12ZW5kb3I6IHZ1ZS9waW5pYS92dWUtcm91dGVyL2F4aW9zIFx1NTdGQVx1Nzg0MFx1Njg0Nlx1NjdCNlx1RkYwOFx1OTU3Rlx1NjcxRlx1N0YxM1x1NUI1OFx1RkYwOVxuICAgIC8vIC0gdmVuZG9yOiBcdTUxNzZcdTRGNTlcdTdCMkNcdTRFMDlcdTY1QjlcdTRGOURcdThENTZcbiAgICAvLyBcdTZDRThcdTYxMEZcdUZGMUFWaXRlIDggKHJvbGxkb3duKSBcdTRFMERcdTY1MkZcdTYzMDEgbWFudWFsQ2h1bmtzIFx1NUJGOVx1OEM2MVx1NUY2Mlx1NUYwRlx1RkYwQ1x1NUZDNVx1OTg3Qlx1NzUyOFx1NTFGRFx1NjU3MFx1NUY2Mlx1NUYwRlxuICAgIHJvbGx1cE9wdGlvbnM6IHtcbiAgICAgIG91dHB1dDoge1xuICAgICAgICBtYW51YWxDaHVua3MoaWQpIHtcbiAgICAgICAgICBpZiAoIWlkLmluY2x1ZGVzKCdub2RlX21vZHVsZXMnKSkgcmV0dXJuIHVuZGVmaW5lZFxuICAgICAgICAgIGlmICgvW1xcXFwvXW5vZGVfbW9kdWxlc1tcXFxcL10odnVlfHBpbmlhfGF4aW9zfHZ1ZS1yb3V0ZXIpW1xcXFwvXS8udGVzdChpZCkgfHxcbiAgICAgICAgICAgICAgL1tcXFxcL11ub2RlX21vZHVsZXNbXFxcXC9dKEB2dWVbXFxcXC9dKS8udGVzdChpZCkpIHtcbiAgICAgICAgICAgIHJldHVybiAndnVlLXZlbmRvcidcbiAgICAgICAgICB9XG4gICAgICAgICAgcmV0dXJuICd2ZW5kb3InXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0sXG4gICAgY2h1bmtTaXplV2FybmluZ0xpbWl0OiA3MDAsXG4gIH0sXG4gIHRlc3Q6IHtcbiAgICBlbnZpcm9ubWVudDogJ2pzZG9tJyxcbiAgICBnbG9iYWxzOiB0cnVlXG4gIH1cbn0pXG4iXSwKICAibWFwcGluZ3MiOiAiO0FBQXNULFNBQVMsZUFBZSxXQUFXO0FBQ3pWLFNBQVMsb0JBQW9CO0FBQzdCLE9BQU8sU0FBUztBQUZtTCxJQUFNLDJDQUEyQztBQUtwUCxJQUFPLHNCQUFRLGFBQWE7QUFBQSxFQUMxQixNQUFNO0FBQUEsRUFDTixTQUFTLENBQUMsSUFBSSxDQUFDO0FBQUEsRUFDZixTQUFTO0FBQUEsSUFDUCxPQUFPO0FBQUEsTUFDTCxLQUFLLGNBQWMsSUFBSSxJQUFJLFNBQVMsd0NBQWUsQ0FBQztBQUFBLElBQ3REO0FBQUEsRUFDRjtBQUFBLEVBQ0EsUUFBUTtBQUFBLElBQ04sT0FBTztBQUFBLE1BQ0wsUUFBUTtBQUFBLFFBQ04sUUFBUTtBQUFBLFFBQ1IsY0FBYztBQUFBLE1BQ2hCO0FBQUEsSUFDRjtBQUFBLEVBQ0Y7QUFBQSxFQUNBLE9BQU87QUFBQSxJQUNMLFFBQVE7QUFBQSxJQUNSLGFBQWE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBLElBS2IsZUFBZTtBQUFBLE1BQ2IsUUFBUTtBQUFBLFFBQ04sYUFBYSxJQUFJO0FBQ2YsY0FBSSxDQUFDLEdBQUcsU0FBUyxjQUFjLEVBQUcsUUFBTztBQUN6QyxjQUFJLDBEQUEwRCxLQUFLLEVBQUUsS0FDakUsb0NBQW9DLEtBQUssRUFBRSxHQUFHO0FBQ2hELG1CQUFPO0FBQUEsVUFDVDtBQUNBLGlCQUFPO0FBQUEsUUFDVDtBQUFBLE1BQ0Y7QUFBQSxJQUNGO0FBQUEsSUFDQSx1QkFBdUI7QUFBQSxFQUN6QjtBQUFBLEVBQ0EsTUFBTTtBQUFBLElBQ0osYUFBYTtBQUFBLElBQ2IsU0FBUztBQUFBLEVBQ1g7QUFDRixDQUFDOyIsCiAgIm5hbWVzIjogW10KfQo=
