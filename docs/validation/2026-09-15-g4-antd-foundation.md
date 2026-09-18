# 验证报告 — G4 基座基建（refactor-myquant-frontend-f1-f7 阶段 B 起点）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G4_

## 冻结产出（回写 design.md G4 节）

- 色彩映射表：@theme 品牌组 → ConfigProvider token（colorPrimary=#F59E0B/profit #00C853/loss #EF4444 语义延续仅载体变）；chart 九色与 glow 不入 token（G14 集中常量/GlassCard 层）
- 组件映射表：被消费 13 组件 → AntD 对应；未消费 6 组件 G13 直删；ConfirmDialog→App.useApp().modal；sonner→AntD message/notification（G16 批）
- G-4 裁决：保留 font 双族+radius 四档；--color-* 视觉变量过渡期保留（旧 JSX 仍消费），删除断点=G13

## 实施落地

- antd 6.6.4 入 dependencies
- src/theme/antdTheme.ts——theme token 单源（darkAlgorithm + 17 项 token）
- main.tsx——ConfigProvider 唯一挂载（theme+zhCN locale）+ AntdApp 包裹（App.useApp modal/message 体系前置）
- index.css——Tailwind v4 分层引入（theme+utilities，跳过 preflight：AntD reset 优先）

## 冒烟记录

- 主题生效：preview 产物 CSS 实证 `#f59e0b` token 变量注入（ConfigProvider CSS-in-JS 生效）
- ant-btn 等组件类零命中=过渡态预期（业务页未迁，G5 起逐页替换）

## 四件套

tsc 0 / build ✓（383ms）/ vitest 27 文件 258 全绿 / lint 78 errors（=F0 基线零新增）/ knip 与 G1 基线零差异
