# B4 双源消除 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B4 · tag BE4 · 2026-09-16
验证：后端全量 pytest **2162 passed**；行为等价（零行为变更项，全部为纯位移/归一）。

## 7.1 数据域合并（4 项裁决执行）

| 双源 | 处置 | 行为对照 |
|---|---|---|
| load_mtf_data 双源（data/mtf_loader:84 vs validation/mtf:45） | 勘察修正为**不同域加载器撞名**：validation 版改为内部使用共享 `find_parquet`（含别名兜底）消除手拼路径；签名保持、三调用方零改动；merge_to_base/get_timeframe_minutes 保留（域语义函数非双源） | 等价（裸读语义不变，路径发现增强别名兜底与主链一致） |
| TF 表 ×3（trading `_BAR_SECONDS`/evolution `_TF_DURATION_MINUTES`/validation `_TF_MINUTES`——勘察比骨架多一张） | 归一为 `core/data/timeframes.TF_SECONDS` **并集**（14 周期）；trading 直引、validation 分钟表推导、evolution 排序键换算 | 等价（`.get` 兜底默认不变；并集新增键无调用方） |
| _now/_now_iso ×5 | 归一为 `core/persistence/timestamps.utc_now_iso`（repository/双 runner 统一引用；db_ext legacy 链随迁移工具保留；db.py 死 _now 删除） | 逐字等价（同一 `datetime.now(timezone.utc).isoformat()`） |
| init_cash 默认分叉（100000 vs 10000） | B4.0 裁决**保留**——两域业务默认不同是事实语义，统一=行为变更 | 不动 |

## 7.2 逻辑域合并（裁决执行 + 2 项实态修正）

- TaskController 双源（api/runner:55 与 trading/runner:48）→ `core/concurrency/task_control.py`（TaskStopRequested+TaskController 同一套，两 runner import；42 相关测试绿）
- SSE 脚手架：B4.0 裁决不动（B8 socket.io 切换时整体消亡）
- bar 循环双实现：形制级相似非逻辑双源，登记不合并
- 冠军双轨：勘察未见（champion.py 单源 ChampionTracker），实态修正为不存在
- 回测编排 5→1：**实态修正**——当前为三处同构构造（单回测/compare/batch 均三参）+ backtest_service 辅助集已在用；「5 处编排」不存在，数据加载链各有语义，强行归一违背行为等价优先。**裁决：登记已完成/不适用**（design.md B4.0 ⑨ 补记）
