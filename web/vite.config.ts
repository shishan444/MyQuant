/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import babel from "@rolldown/plugin-babel";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [
    react(),
    // React Compiler（G15）：vite 8 官方路径 = plugin-react v6 reactCompilerPreset
    // + @rolldown/plugin-babel 显式 opt-in。compilationMode 用默认 infer：
    // 只编译组件与 use* hook，普通导出函数（utils/services 纯函数在渲染
    // 上下文外被调用）不编译——all 模式会给它们插 useMemoCache 导致非渲染
    // 上下文调用即崩（实测 115 用例失败）；测试源码同样不适用，一并排除
    await babel({
      include: ["src/**/*.ts", "src/**/*.tsx"],
      exclude: ["src/test/**"],
      presets: [reactCompilerPreset()],
    }),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
