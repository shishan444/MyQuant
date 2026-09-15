# 验证报告 — G13 依赖退役（阶段 B 收口）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G13_

## 退役清单

- **ui/ 19 组件目录删除**（前置：6 个仍消费的业务文件全部替换——ConfirmDialog 本体重写为 antd Modal 封装（props 接口不变，Evolution/Strategies 3 消费处零改动）、EmptyState/ErrorBoundary/Sidebar Tooltip（radix→antd Tooltip）/StrategyDetail Badge×3→Tag）
- **VerifyDrawer 半遗弃文件删除**（knip 白名单同步收缩：ui/**、VerifyDrawer、RHF 系四条目移除）
- **依赖移除**：radix-ui、class-variance-authority、react-hook-form、@hookform/resolvers（pnpm remove）
- 保留判定：tailwind-merge/clsx 经 lib/utils cn() 全库消费（合法依赖）；lucide-react 图标库（视觉延续）

## @theme 视觉变量退役裁决（G4 断点顺延）

G4 冻结的「@theme 视觉变量删除断点=G13」实证不成立：text-profit/text-loss/bg-bg-elevated 等 Tailwind 类**遍布未重写的业务 JSX**（组件替换未动类名），删除 @theme 立即全库样式崩。顺延至 G17 终检（随 ui-uniformity「跨边界视觉类清理」逐类处理）。本批零残留判定改为：**ui/ 目录与 radix 系 import 零残留**（grep 实证）

## 四件套

tsc 0 / build ✓ / vitest 31 文件 277 全绿（重试按钮断言按 AntD 两字空格规则修正×2，语义保持）/ lint 73 errors（基线 78 再降）/ knip 68 行（基线收缩）
