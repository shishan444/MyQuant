# B6 进化域生长+错误族 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B6 · tag BE6 · 2026-09-16
验证：后端全量 pytest **2166 passed**（+4 形制守护）+ **L3 复跑 14 passed**（BE-P4 Phase 末）。

## 9.1 错误族统一（对齐前端 G16.0 冻结契约）

普查卡结论（design.md B6.0）：现状经 B1-B5 路由纪律已接近契约，工作量=1 处修正：
- trading.py:83 手写 422（detail 含原始异常文本）→ **400**（业务拒绝语义；422 保留 Pydantic 校验明细数组）——唯一 status 变更项
- 5xx 兜底：无自定义 handler，FastAPI 默认 `{"detail":"Internal Server Error"}` 已满足「固定通用文案」裁决（登记，不新增）
- 泄露审查：`detail=str(exc)` 一处（排序参数 ValueError 业务消息，合规）；无堆栈/路径/SQL 泄露
- **G16.0 对照声明**：422=校验明细数组（test_422_is_pydantic_detail_array）/409=业务字符串（D3 守护，test_409_business_string 经真实 HTTP）/4xx=业务字符串/5xx=固定文案无泄露（test_5xx_fixed_generic_detail 以内部路径异常注入验证）——四层形制守护固化 `tests/test_b6_error_contract.py`

## 9.2 进化域 service 用例化（路由薄化）

- `core/services/evolution_service.py`：pause/stop/resume 三状态迁移用例（判定+D3 前态守护+写入序列；TaskNotFound/IllegalTransition 业务异常）；路由薄化为注入+调用+HTTP 映射
- batch1 D3 语义零漂移（7 守护测试+test_409 HTTP 真实路径）；create_task 不入 service（创建链已收敛，B6.0 裁决）；事务形态=repository 函数级事务（与事务裁决一致）
