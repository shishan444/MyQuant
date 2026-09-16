# refactor-myquant-frontend-f1-f7 容器总结（G17.3 · 容器完成判据）

日期：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | 31/31 任务完成

## 容器完成判据（全绿）

| 判据 | 终值 |
|---|---|
| tsc -b --force | **0 错误** |
| vitest | **34 文件 315 全绿**（容器起点 ~270 → 315） |
| eslint | **86 problems（73e+13w）**，与 F0 基线 78 持续下降后持平（G13 后 73e 稳定） |
| build | ✓（2.4s） |
| knip | 唯一名 diff **零新增**（G1 基线只减不增） |
| L3 e2e | **14 passed**（前后端真实服务） |

## 17 组证据汇总（docs/validation/，tag FE-G1..FE-G17）

| 组 | tag | evidence | 核心交付 |
|---|---|---|---|
| G1 契约生成 | FE-G1 | 2026-09-15-g1-contract-generation.md | openapi-typescript 生成链（50 路径/76 模型、幂等）、api.ts 语义别名层（104 drift 修正）、knip 基线 |
| G2 实时契约 | FE-G2 | 2026-09-15-g2-realtime-contract.md | 事件契约文档+zod 判别联合+连接适配层双实现+SSE 统一封装+三实证缺陷修复+四通道真实冒烟 |
| G3 pnpm | FE-G3 | （提交组内） | 519 包零漂移等价迁移 |
| G4 基座 | FE-G4 | （提交组内+design.md G4 节） | AntD ConfigProvider 唯一挂载+theme 单源+Tailwind 分层跳 preflight+色彩映射表冻结 |
| G5-G12 八页 | FE-G5..12 | g5..g12 各组文档 | 逐页 AntD 化+表单 Form 化+SSE 接流式接口+URL 化状态所有权+组件归位；G12 含 SignalGeneCondition 阈值键 value→threshold 实证修正（对齐后端 executor.py:52-63） |
| G13 依赖退役 | FE-G13 | 2026-09-15-g13-deps-retirement.md | ui/ 19 组件+VerifyDrawer 删除（-4029 行）、radix/cva/RHF 系移除 |
| G14 图表动效 | FE-G14 | 2026-09-15-g14-charts-motion.md | recharts→echarts 按需引入+权益曲线 3 实现→1 唯一化+图表色集中常量；framer-motion→motion/react+useReducedMotion 降级 |
| G15 Compiler | FE-G15 | 2026-09-15-g15-react-compiler.md | reactCompilerPreset+@rolldown/plugin-babel 官方路径；infer 模式实证（all 不可用于业务库）；memo 清理计划 |
| G16 错误+ky | FE-G16 | 2026-09-15-g16-error-contract-ky.md | 拦截器五层契约（ApiError）+axios→ky 62 调用点+「结果未知」语义+13 错误场景用例 |
| G17 终检 | bc83c90/0608378/本提交 | g17-1/g17-2/本文件 | 七步逐页终检+持久化审计+zustand 双维终核（2 selector 化+1 豁免落档）+@theme 桥接层裁决；L3 14 条+截图矩阵+视觉抽查 |

## 后端容器移交清单（refactor-myquant-backend-b1-b11 启动时消化）

1. **B8 socket.io 双端批（按 G2 冻结契约实施）**
   - 契约路径：`web/src/realtime/contract.md`（事件模型共同权威：事件名/payload schema/zod 判别联合）
   - 前端就位状态：`connection.ts` 适配层双实现（原生 WS=启用中 / socket.io-client=已安装，
     `VITE_REALTIME_TRANSPORT=socket.io` 特性开关切换）；后端实现 socket.io 服务端后，
     前端零代码改动（仅环境变量切换）——切换清单详见 g2 evidence
2. **B6 错误契约对齐（G16.0 冻结文本为目标）**
   - 后端 detail 保持 `string | [{loc,msg}]` 形态（前端 422 展开/4xx 透出已按此实现）
   - 5xx 不回敏感 detail（前端已兜底过滤，但根治在后端）；409 携带业务语义字符串
   - 契约全文：design.md G16 节
3. **S1 凭证回显风险（Settings 币安凭证表单）**
   - 前端无修复项（表单已 AntD Form 化+前端不明文展示已存凭证）；风险在 GET /api/config
     是否回传凭证明文——后端批需裁决回显策略（建议脱敏/不回传）
4. **知会项**：SignalGeneCondition 阈值键已实证为 `threshold`（后端
   `core/strategy/executor.py:52-63` 真实消费），前端全量已对齐——无后端动作

## 遗留与建议（后续批，非本容器范围）

- 视觉建议项（g17-2 落档）：Trading 空态按钮主色化、次要文字对比度、数值列对齐统一
- memo 清理批（g15 落档）：2 处手动 memo + 高频手写 useMemo/useCallback 精简（以 Profiler 实测为准）
- openspec 全目录无版本控制（用户已知情，独立决定待裁决）
