# 实时事件契约（G2 冻结 · 2026-09-15）

> 本文档是前端消费与后端端点实现的**共同权威**（refactor-myquant-frontend-f1-f7 · G2 契约先行；
> 后端容器 B8 批照此实现 socket.io 端点）。
> 字段全部从当前代码实证（实证位置随条目标注）：trading=core/paper_trading/runtime/executor.py+api/realtime.py、
> 连接层=api/realtime.py。
> 当前载体为 socket.io（B8.4 起全量切换；native WS 与 SSE 流式端点已退役）。

> **2026-09-15 后端容器方案设计确认（用户裁决）**：四通道**全量切换** socket.io（含 verify/batch 流式通道，
> 不保留 SSE 长期双载体）；流式子批施工形态=POST 返回 `{session_id}` + socket.io emit（namespace `/streams`、
> room=session_id、事件名 progress/complete/error 与 SSE 帧同名）。取消/断线语义由 B8.0 冻结时在本文件补注
> （裁决方向：C→S cancel 事件协作取消；断线不丢结果，任务跑完落库经 HTTP 查询面恢复）。
> native WS 与 SSE 端点在切换验证通过后于 B8 组内退役。

## 通道

| 通道 | 当前载体 | socket.io 目标形态 | 订阅粒度 |
|---|---|---|---|
| trading | socket.io namespace `/trading` | namespace `/trading`，room `task_id` | 每任务 |

<!-- verify/batch 流式通道两行（POST /api/strategies/verify/stream 与
     /api/strategies/batch-backtest/stream，SSE 响应）已随流式端点退役删除
     （fix-replay-gap-alignment——2026-11-22）；404 护栏见
     tests/api/test_retire_verify_sessions.py -->

## 连接层协议消息（适配层消费，禁止进入业务缓存）

> 现状注记：socket.io 形态下协议消息由其内建机制承接（见文末 B8.4 落档）；
> 下表实证列的 ws.py 已随 native WS 端点退役删除，消化语义仍由前端
> native 逃生口（realtime/connection.ts）实现。

| 消息 | payload | 方向 | 实证 |
|---|---|---|---|
| `subscribed` | `{ type, task_id }` | S→C（连接确认） | ws.py:109 |
| `ping` | `{ type: "ping" }` | C→S（心跳，20s 间隔，适配层职责） | ws.py:118 |
| `pong` | `{ type: "pong" }` | S→C | ws.py:120 |

<!-- evolution 通道与 /streams 流式契约段已随后端域退役删除
     （retire-legacy-subsystems B1/任务 11——2026-11-16）；前端消费面清理归 F 面承接 -->
## trading 通道业务事件（S→C，模拟交易域七事件）

> rebuild-paper-trading 切换（2026-10-17）：旧三事件（task_started 旧形 /
> task_snapshot 旧形 / position_update）全退役，换新七事件族。
> 构造点实证：core/paper_trading/runtime/executor.py `_push`（:409/:439/:674/:812/:858/:863/:867）+
> api/realtime.py `_emit_task_snapshot`（/trading 分支）。连接形制与频道不变
> （namespace /trading、room=task_id）。

### task_started（executor.py:439，pending→running 迁移完成后）
```json
{ "type": "task_started", "task_id": string }
```

### task_snapshot（api/realtime.py，connect 即下发；任务存在即发，五态 status 自解释）
```json
{ "type": "task_snapshot", "task_id": string, "status": string,
  "equity": number|null, "balance": number|null, "total_trades": number }
```
equity 派生与 AccountSnapshot.equity 同式（balance+margin+未实现盈亏；账户组 NULL→None 透传非 0 假值）。

### task_update（executor.py:812，每 bar 原子落库后）
```json
{ "type": "task_update", "task_id": string, "status": "running",
  "equity": number, "balance": number, "unrealized_pnl": number,
  "bars_processed": number, "window_bars": number|null }
```

### fill_event（executor.py:674，逐笔成交）
```json
{ "type": "fill_event", "task_id": string, "bar_time": string, "seq": number,
  "kind": string, "side": string|null, "price": number|null,
  "quantity": number|null, "fee": number|null,
  "realized_pnl": number|null, "reason": string|null }
```
kind=Intent 九值（open/close/add/reduce/exit_sl/exit_tp/exit_max_hold/exit_time/exit_liquidation）；出场类 side 为 null。

### task_completed（executor.py:858，窗口到点终态）
```json
{ "type": "task_completed", "task_id": string,
  "final_equity": number, "total_trades": number }
```

### task_stopped（executor.py:863，协作停/止损阈值终态；stop_reason 键恒在——当前全部调用路径为字符串（user_stop/stop_condition），schema nullable 为宽容超集）
```json
{ "type": "task_stopped", "task_id": string, "stop_reason": string|null }
```

### task_error（executor.py:409 装载失败早退 / :867 执行异常终态）
```json
{ "type": "task_error", "task_id": string, "error_detail": string }
```

<!-- 流式通道事件契约段（verify/batch 共用形制，SSE event: 帧
     progress/complete/error）已随流式端点退役删除
     （fix-replay-gap-alignment——2026-11-22，同上文通道表去向注） -->

## 消费纪律（ws-message-consumption spec 的实现约束）

1. 每条业务消息先经 zod 校验（schemas.ts）再分发；校验失败丢弃并留痕（console.warn，含通道/事件类型/错误）
2. 协议消息（subscribed/pong）由适配层消化，业务层不可见
3. 消息分发无 else 兜底合并；未识别事件丢弃留痕
4. 重连排程与业务防抖失效为两个独立定时器，互不覆盖
5. chart-indicators 为轮询 GET（非流式），不在本契约范围

> **B8.4 终态落档（2026-09-16）**：四通道已全量切换 socket.io——native WS
> 端点（/ws/evolution /ws/trading）与 SSE 流式响应体退役删除；连接即下发
> task_snapshot（重连恢复快照语义迁移至 /evolution /trading connect）；
> 连接层协议消息（subscribed/ping/pong）由 socket.io 内建机制承接；
> 前端默认 transport=socketio（VITE_REALTIME_TRANSPORT=native 为无服务端
> 支持的本地逃生口）。
