# B3a ORM 基建+dataset_meta 试点 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B3a · tag BE3a · 2026-09-16
验证：后端全量 pytest **2159 passed**（`--ignore=tests/e2e` 口径）；新增 `tests/test_orm_infra.py` 14 用例。

## 3.1 基建落地

- 依赖：SQLAlchemy 2.0.54 + Alembic 1.20.0（pyproject.toml + requirements.txt 双源同步，batch1 D5 纪律）
- `core/persistence/orm/`：models.py（**11 表全量声明**，PRAGMA 驱动机械生成+人工校）+ engine.py（create_db_engine：WAL + busy_timeout=5000 + foreign_keys=ON + check_same_thread=False；create_session_factory：expire_on_commit=False/autoflush=False）
- 对照记录：ORM DDL vs legacy 轨（init_db_ext + live 表）**全列全等**（类型/默认值/notnull），唯一豁免=9 处 PK 列 notnull（SQLite `TEXT PRIMARY KEY` 怪癖：legacy PRAGMA 显示 0，ORM 渲染 NOT NULL——语义等价且更严格），固化为 `TestModelMatchesLegacySchema` 守护
- 类型映射纪律：TEXT→Text / REAL→REAL（渲染回 PRAGMA 字面量全等的前提）；可空列 `Mapped[Optional[...]]`（SQLAlchemy 2.0 非 Optional 注解推导 nullable=False 的坑）

## 3.2 Alembic 接管与三轨归一

- baseline revision `ae6e14d43dc5`（autogenerate 对空库产出 11 表全量 DDL）；env.py 解析序 `-x db_path > MYQUANT_DB_PATH > data/quant.db`
- 接管流程 `scripts/adopt_alembic_baseline.py`：**先 legacy 轨补齐（init_db_ext+live 表，幂等）后 stamp head**——存量库可能有 B1.3 唯一索引等未跑增量，stamp 语义=与 head 等价，故必须先补齐
- 验证矩阵（固化为 `TestAlembicBaseline`）：①新库 upgrade head 业务表 schema 与 legacy 全等（alembic_version 自身除外）②legacy 库 adopt 后 schema 不动+版本=head ③再生成幂等（autogenerate 零变更）
- 三轨归一落点：Alembic 版本链=唯一演进轨；SQL 轨 005/006 头部标记文档性废弃（B3d 随旧轨删除）；Python ALTER 轨冻结不再新增；live_trading_task 经模型纳入 baseline（收编）

## 3.3 dataset_meta 试点

- `core/persistence/repository/dataset_meta.py`：五函数（save/get/list/update_stats/delete）SQLAlchemy Core 实现，函数级事务（非进化域终态裁决）
- db_ext 同名函数改一行委托（签名不变），调用方（api/routes/data.py 等）零改动
- 等价锚定：裸 INSERT 重复主键 IntegrityError（调用方先 get 后 save 的使用序不变）、白名单外字段拒绝更新、list 过滤/排序/limit 行为不变
- 副本库演练（`test_adoption_path_on_copy_of_production_db`）：quant.db 副本 adopt 后，repository 与旧轨读到的行集一致、schema 无损
