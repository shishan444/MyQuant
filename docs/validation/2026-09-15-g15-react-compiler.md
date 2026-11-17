# G15 React Compiler 接入 Evidence

日期：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G15

## 接入方式（vite 8 官方路径）

vite 8（rolldown 内核）+ @vitejs/plugin-react v6：**babel 选项已被移除**（oxc 直编译），
React Compiler 官方接入路径 = plugin-react v6 导出的 `reactCompilerPreset` helper +
`@rolldown/plugin-babel` 显式 opt-in（vite 8 发布说明原文路径）。

```ts
// vite.config.ts
plugins: [
  react(),
  await babel({               // ⚠ babel() 返回 Promise<Plugin>，必须顶层 await
    include: ["src/**/*.ts", "src/**/*.tsx"],
    exclude: ["src/test/**"],
    presets: [reactCompilerPreset()],   // compilationMode 默认 infer
  }),
  tailwindcss(),
],
```

## 接入过程的三次实证修正（踩坑记录）

| # | 症状 | 根因 | 修正 |
|---|---|---|---|
| 1 | build ✓ 但产物无 compiler 痕迹 | `babel()` 返回 **Promise\<Plugin\>**，vite 静默跳过未 await 的插件 | 顶层 `await babel({...})` |
| 2 | 产物 grep "compiler-runtime" 0 命中，疑似未编译 | bundled 单文件里 import 已转 CJS interop（`require_compiler_runtime`），路径字符串不保留——**grep sentinel 错误** | 正确 sentinel：minify:false 产物中 `require_compiler_runtime` / runtime 实现特征 `Array(N).fill` |
| 3 | `compilationMode: "all"` 下 115 用例崩溃（`Cannot read properties of null (reading 'useMemoCache')`） | react 19.2 的 `c()` 是**真 hook**（`ReactSharedInternals.H.useMemoCache`）；all 模式编译**普通导出函数**（utils/services 纯函数、测试辅助函数），在非渲染上下文调用即崩 | 用默认 **infer** 模式（只编译组件与 use* hook）+ exclude `src/test/**` |

结论：**业务代码库不可用 all 模式**（all 是框架场景），infer 是业务正确形态。

## 生效证据链

1. 最小复现（rolldown 直测）：infer 模式下含组件文件产物带 `require_compiler_runtime` ✓；必抛错 babel 插件触发证明 babel 链执行 ✓
2. 主项目 build 产物特征：`Array(N).fill`（compiler-runtime 实现痕迹）×1、`_c(`（cache 调用）×29 ✓
3. vitest 走同一 vite 插件链 → **33 文件 302 用例在编译产物下全绿**（组件/hooks/页面/AntD 交互用例全量兼容实测）

## 四件套

tsc -b **0** / vitest **33 文件 302 全绿** / lint **86（73e+13w，基线持平）** / build ✓（2.25s）

## 兼容性浏览器冒烟（真实前后端服务）

| 检查项 | 结果 |
|---|---|
| Evolution 任务详情抽屉（AntD Drawer + tab 切换） | 正常打开，种群快照/进化曲线 tab 在 |
| 进化曲线图表（echarts，Compiler 编译产物下） | canvas painted=true，legend 齐全 |
| Settings 三表单（AntD Form ×3：8 inputs / 2 switches / 币安表单/保存按钮） | 渲染完整 |
| AntD Switch 受控交互 | 点击后 aria-checked true→false 翻转 ✓（测后还原） |
| console（全 session 含五页导航） | **零 error 零 warning** |

## 存量 memo 清理计划

| 项 | 现状（src 非测试代码） | 处置 |
|---|---|---|
| `React.memo` 组件 | 2 处：`evolution/StrategyList.tsx` StrategyRow、`evolution/HistoryTable.tsx` HistoryRow | **本容器保留**：Compiler 自动 memo 化后手动包裹属冗余，但保留无行为风险；删除属独立优化批（需列表滚动/交互回归），不在本容器引入额外变量 |
| `useMemo` | 51 处 | 保留：编译器对手写 memo 兼容；新代码不再需要手写 |
| `useCallback` | 63 处 | 同上 |

后续批（G17 容器完成后）：评估删除 2 处手动 memo + 高频列表页手写 useMemo/useCallback 精简——以 React DevTools Profiler 实测收益为准，不盲删。

## 依赖变更

- devDependencies +：`babel-plugin-react-compiler@1.0.0`、`@rolldown/plugin-babel`
- vite.config.ts：插件链 react() + babel(compiler preset) + tailwindcss()
