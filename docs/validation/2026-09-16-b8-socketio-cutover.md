# B8 交易域对齐+socket.io 双端批 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B8 · tag BE8 · 2026-09-16
验证：后端全量 **2153 passed**（退役 native WS 测试 15 个）+ 前端三件套（tsc ✓ / vitest 314 ✓ / lint 零增量）+ **L3 14 passed（默认 socketio 形态）**。

## 四证明记录（B8.0 终核）
①原生 WS 无自动重连/房间语义（前端 connection.ts 手写重连排程自供状）；②双端各自维护连接管理成本高于引入；③python-socketio ASGI 原生生态兼容；④受控入口=manager.push 单点（实施中演进为 push 闭包单点）。

## 四语义点落地
| 语义点 | 落地 |
|---|---|
| ①取消 | C→S `cancel` 事件（/streams，payload {session_id}）→ stream_control 注册表步粒度协作取消；前端 AbortSignal→emit cancel |
| ②断线 | 流式任务跑完并落库（verify_session 持久化）；恢复面=既有 `GET /verify/sessions/{id}/results`（零新增端点）；WS 通道重连快照=connect 即 emit task_snapshot（语义自 native 迁移） |
| ③事件命名 | /evolution /trading /streams + room=task_id/session_id；业务事件名与 payload 不变（onAny+type 判别）；协议消息（subscribed/ping/pong）由 socket.io 内建承接 |
| ④时间戳 | payload 原样透传（broadcast 不做格式化），前端 zod 全 string 无 Date 路径 |

## 施工与退役
- 11.2：WS 双发桥接（L3 env 切换验证 14 passed）→ 11.3：流式双端批（POST+{session_id}+后台 emit；前端 sse.ts 内部换实现签名不变）→ 11.4：**默认形态切 socketio**（前端默认分支反转，native 为无服务端逃生口）、native WS 端点/manager/SSE 流式响应体删除、ws.py 退役、契约终态落档 contract.md
- 测试重锚定：sse.test 按新实现重写（4 用例）、connection.test 默认分支反转、hooks 测试 stubEnv native（锚定实现面）、native WS 测试退役 15 个（连接/快照/manager/REST-WS 一致性）

## 双形态切换对照
| 验证项 | native（B8 前） | socketio（B8 后默认） |
|---|---|---|
| 后端全量 | 2168 passed | 2153 passed（−15 退役） |
| 前端 vitest | 315 | 314（重锚定） |
| L3 主链路 | 14 passed | 14 passed |

## 11.1 对齐（前置于施工）
live/模拟盘纯计算边界文档化（MAP.md：共享=judgment evaluate 纯函数+types，零状态共享）；双 runner 基建经 B4.2 已同构。
