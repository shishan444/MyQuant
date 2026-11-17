# 安全链路四图（B9.1 落档，2026-09-16）

> 本地单用户工具的威胁模型（design.md B9 节裁决）：无认证面（单用户本地
> 进程）、无公开部署；敏感面=API 凭证与文件系统信任边界。

## 图一 · 入口面（外部输入进入处）

```
浏览器(Vite :5173) ──HTTP──> FastAPI(:8000, CORS localhost)──> api/routes/*
                              │  输入解析：Pydantic 校验（422 细节数组）
                              │  路由层无业务判定（B6 service 化后）
K 线数据 ──币安公共 API──> core/data（无需 Key）
实盘行情 ──币安私有 API──> core/live_trading/binance_client（凭证经环境变量/config.json，执行放行关闭）
```

## 图二 · 信任边界

| 边界 | 输入源 | 防线 |
|---|---|---|
| HTTP 入口 | 浏览器 fetch/axios | Pydantic 校验；错误体不泄露内部（B6 四层形制，5xx 固定文案） |
| 文件系统边界 | `data/config.json`（明文凭证） | **本地单用户威胁模型下接受**（裁决：文件加密收益低于复杂度）；多用户/共享主机=触发条件（届时立项加密） |
| 环境变量边界 | `BINANCE_API_KEY` 等 | 启动读取，禁止硬编码（MAP.md 约束） |
| 出站边界 | 币安 API 调用 | core/live_trading 网关唯一出口；模拟盘/回测零出站 |

## 图三 · 敏感数据流（凭证）

```
config.json(明文) ──GET /api/config──> masked 回显（前4后4中***，S1 修复）
                     PUT 含*** =哨兵不覆盖（S1）
环境变量 ──> binance_client ──> 出站请求头（不落日志：logging 层无请求头输出）
日志面：core.logging 只记业务事件（task/phase/generation），无凭证字段（grep 实证）
```

## 图四 · 凭证位置清单

| 位置 | 形态 | 状态 |
|---|---|---|
| 源码 | 无硬编码（grep binance_api_key= 字面值仅 config.py 的 _DEFAULT_CONFIG 空串默认） | ✓ |
| config.json | 明文存储（本地威胁模型接受，图二） | 登记 |
| 环境变量 | BINANCE_API_KEY/SECRET、ANTHROPIC_API_KEY | ✓ 规范路径 |
| 日志 | 零命中（凭证键不进任何 logger 输出） | ✓ |
| 测试 fixture | tests/test_b9_s1_credentials.py 用占位值（AbCdEf...非真实凭证） | ✓ |

## 对照核心纪律落点

- 认证/会话/token：不适用（单用户本地，无认证面）——公开部署为触发条件
- 错误语义：B6 四层形制（422 数组/409 字符串/5xx 固定）已落地有测试
- 凭证回显：S1 masked+哨兵往返测试（tests/test_b9_s1_credentials.py 5 用例）
- 出站请求：唯一网关+执行放行关闭（app 无 live 执行启动路径）
