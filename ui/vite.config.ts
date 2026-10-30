import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/** file:// 下 module script 会被 CORS 拦（origin null）。
 *  产物本身已是 IIFE，这里把入口标签改回经典 script 即可。 */
function classicScript(): Plugin {
  return {
    name: "classic-script",
    enforce: "post",
    transformIndexHtml(html) {
      return html.replace(/<script type="module" crossorigin/g, "<script defer");
    },
  };
}

// 产物形态由桌面端加载方式决定（见 docs/superpowers/specs/2026-10-29-desktop-ui-react-antd.md 第 6 节）：
// pywebview 目前用 html= 内联，AntD bundle 会顶到 WebView2 NavigateToString 的 2MB 上限；
// 首选方案是 file:// 加载，而 file:// 下 ESM 会被 CORS 拦，所以固定输出 IIFE、相对路径、文件名带 hash。
export default defineConfig({
  base: "./",
  plugins: [react(), classicScript()],
  build: {
    outDir: "../convcore/webui/static/gui",
    emptyOutDir: true,
    target: "es2018",
    modulePreload: false,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "assets/app-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
