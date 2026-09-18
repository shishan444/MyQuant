# 验证报告 — G2 实时层契约先行（refactor-myquant-frontend-f1-f7 阶段 A）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G2_

## 事件契约（冻结交付物）

- 文档：`web/src/realtime/contract.md`——两 WS 通道（evolution 7 业务事件 / trading 3 业务事件）+协议消息族+流式通道事件族，payload 全部代码实证（api/runner.py、core/trading/runner.py、api/routes/ws.py）
- zod schema：`web/src/realtime/schemas.ts`（判别联合+validate 入口；嵌套体 record 放行防误杀）
- 盘点修正：chart-indicators 实为轮询 GET 非流式，原「三 SSE 流」更正为两 SSE 流（chart-indicators 不在契约范围）

## 消费层重构（实证三缺陷全修）

| 实证缺陷 | 修复 |
|---|---|
| useEvolution else 兜底把 subscribed/pong 协议消息 spread 进 task 缓存 | 兜底分支删除（switch 穷尽判别联合）；协议消息适配层消化+校验层兜底 |
| useTrading 重连排程与防抖失效共用 reconnectTimer 一个 ref（可互相覆盖致重连丢失） | 双 ref 分离；重连职责整体归适配层 |
| JSON 解析失败静默吞 | 丢弃留痕 console.warn（通道+原因） |

## 连接适配层（`web/src/realtime/connection.ts`）

- 接口：`onMessage / onStatus / close`；消费层对传输实现无感知
- 实现一 · 原生 WS（启用中）：URL 构造/3s 重连/20s 心跳 ping（契约新增，后端 ws.py 原生支持）/协议消息消化
- 实现二 · socket.io-client 4.8.3（就位未启用）：开关 `VITE_REALTIME_TRANSPORT=socketio`；**移交后端容器 B8 批**：实现端点（照 contract.md）+两端同批切换
- 两 hook 已重构为适配层消费（useEvolutionWebSocket / useTradingWebSocket）

## SSE 统一封装（`web/src/realtime/sse.ts`）

- `streamSse(url, payload, handlers, signal)`——verify/batch 两 service 的 95% 重复手写解析收敛为单实现；契约外事件与畸形帧丢弃留痕
- 流式回调类型收编 realtime 层（StreamProgressEvent/StreamHandlers），services re-export 保消费零改动

## 测试

- L1 schema：22 用例（每事件正例/缺字段/类型错误拒绝/champion_dna 宽容/协议分流/留痕）
- L2 适配层：30 用例（双实现同套接口：URL/协议消息不上抛/onStatus 语义/重连/心跳停止/close 幂等/开关分支）
- L2 SSE：5 用例（跨 chunk 拼帧/残留 buffer 末帧/error message 透传/非 ok 响应/abort 静默/契约外丢弃）
- L2 hook：既有用例 fixture 按契约补全（断言语义不变）+新增「协议消息不进业务缓存」用例
- 全量：27 文件 258 全绿

## 真实冒烟（uvicorn 临时起 8901 端口）

| 通道 | 结果 |
|---|---|
| WS /ws/evolution/{id} | ✓ 首帧 `{"type":"subscribed","task_id":...}`（契约协议消息） |
| WS /ws/trading/{id} | ✓ 同上 |
| POST /api/strategies/verify/stream | ✓ 端点活（404 No valid strategies——假 id 业务拒绝，链路通） |
| POST /api/strategies/batch-backtest/stream | ✓ 同上 |

## 四件套

| 项 | 结果 |
|---|---|
| tsc -b | 0 |
| build | ✓（324ms） |
| vitest | 27 文件 258 全绿 |
| lint | 78 errors（=F0 基线零新增） |
| knip | 与 G1 基线零差异（zod 白名单已收缩——转为已消费） |

## 移交后端容器 B8 清单

1. 照 `web/src/realtime/contract.md` 实现 socket.io 端点（namespace /evolution、/trading；流式事件族）
2. 前端切换：`VITE_REALTIME_TRANSPORT=socketio`（适配层已就位，双端同批）
3. 契约文档为共同权威，端点实现不得偏离事件名与 payload 形状
