import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
    // 编辑器/工具的原子写会产生 *.tmpdir / *.tmp / *~RF*.TMP 临时文件，
    // Windows 下被 watcher 碰到会抛未捕获的 EBUSY 直接崩掉 dev server。
    // 注意 .TMP 是大写，必须大小写无关匹配，且要覆盖带 ~ 的临时名。
    watch: {
      ignored: ['**/*.tmpdir/**', '**/*.[tT][mM][pP]', '**/*~*'],
    },
  },
})