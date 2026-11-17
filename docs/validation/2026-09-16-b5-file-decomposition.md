# B5 上帝文件拆解 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B5 · tag BE5 · 2026-09-16
验证：后端全量 pytest **2162 passed** + **L3 复跑 14 passed**（BE-P3 Phase 末，前后端真实服务）。

## 8.1 strategies.py 1693 行 → 五模块包

- `api/routes/strategies/`：crud(170)/backtest(165)/compare(116)/verify(722)/batch(457) + `__init__` 聚合 router；对外 `from api.routes.strategies import router` 与符号引用面（re-export）零改动
- 两个坑与解：①FastAPI 0.137 `include_router` 惰性包装在二次 include 时路由丢失——聚合形制经实验定为「子 include 带 prefix」二层层级；②包内绝对 import 制造双模块身份（`MyQuant.` 前缀 vs 裸）致测试 patch 脱靶——相对 import 统一包身份
- 测试 patch 目标重锚定至子模块（12 处/4 文件）

## 8.2 db_ext 拆 legacy_init + runner 拆 progress 群

- db_ext 1110 → `legacy_init.py` 684（init/ALTER 链+建表+数据修复工具，adopt 流程专用）+ `db_ext.py` 441（纯委托层，`_connect`/`init_db_ext` re-export）
- runner 拆 `api/runner_progress.py`（heartbeat/phase/progress/WS 推送群）；champion_sync 单元经实态修正不拆（B3d 收口后已是一行 repo 调用，再拆=无收益位移——B5.0 裁决在实施中细化）
- 测试 patch 目标重锚定（api.runner._push_ws → api.runner_progress）
