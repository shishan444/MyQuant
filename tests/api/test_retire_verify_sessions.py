"""旧验证会话体系退役特征网（retire-verify-sessions B5）。

契约源：openspec/changes/retire-verify-sessions/（tasks.md 完成标准 +
design.md 验证重点 + specs 三域 delta）。锚组（C1 组）：
- C1-1 五端点退役：四端点 404；POST /api/strategies/verify 被
  /api/strategies/{strategy_id} 动态段吸收实测 405（方法不匹配——
  与 404 同为「verify 路由不存在」证明，完成标准①按此语义落锚）；
  OpenAPI 路由表零 verify 路径（静态证明）
- C1-2 星级五列冻结快照：种子策略带非空星级值 → 普通操作族
  （读/更新/回测/批回测）→ sqlite 直读逐列不变（完成标准②）
- C1-3 VerifySession ORM 停用保留可导入 + verify_session 表留档
  可查 + 仓储模块已删（完成标准⑥/设计定位清单）
- C1-4 四兄弟路由 import 链清洁（模块可导入 + 端点装配在 OpenAPI）
- C1-7 batch 流式事件序列完整（delta-3「流式事件序列完整」
  Scenario——verify 退役后流式比对面=batch：progress×N 后恰一次
  complete，progress.total 与实际次数一致）
- 退役完整性三形制零残留（运行时锚 + 源文本锚）：形制 B 符号
  （_VerifyProcessor/compute_verify_star 等）与形制 C 字面
  （VerifySession/verify_session）在 api/ core/ 代码级扫描零命中
  ——tokenize 剔除 COMMENT/STRING 后扫描，docstring 历史提及不
  触发；形制 A（路由 URL）由 C1-1 OpenAPI 运行时锚承载（装饰器
  URL 是字符串字面量，剔除后不可扫——设计取舍如实披露）。
  core/persistence/orm/models.py 整文件豁免：VerifySession ORM
  停用保留是契约明文（零迁移路线）。
"""
from __future__ import annotations

import importlib
import io
import sqlite3
import time
import tokenize
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna, make_ohlcv

# ── C1-1 五端点（design.md §1：:122/:425/:545/:562/:576）──
RETIRED_ENDPOINTS = [
    ("POST", "/api/strategies/verify"),
    ("POST", "/api/strategies/verify/stream"),
    ("GET", "/api/strategies/verify/history"),
    ("GET", "/api/strategies/verify/sessions"),
    ("GET", "/api/strategies/verify/sessions/sess-legacy/results"),
]

# ── C1-2 星级五列（Q1 选项 A：冻结快照）──
STAR_COLUMNS = (
    "verify_star", "verify_avg_score", "verify_best_score",
    "last_verified_at", "verify_count",
)
# 种子非空星级值（与 lifecycle revalidate 测试的种子形制同源）
_SEED_STAR_ROW = (4, 71.5, 88.25, "2026-10-01T00:00:00", 3)

# ── C1-4 四兄弟路由 ──
SIBLING_MODULES = (
    "MyQuant.api.routes.strategies.crud",
    "MyQuant.api.routes.strategies.compare",
    "MyQuant.api.routes.strategies.backtest",
    "MyQuant.api.routes.strategies.batch",
)
SIBLING_MOUNTED_PATHS = (
    "/api/strategies",                        # crud 列表/创建
    "/api/strategies/{strategy_id}",          # crud 详情/更新/删除
    "/api/strategies/{strategy_id}/revalidate",
    "/api/strategies/backtest",               # backtest
    "/api/strategies/compare",                # compare
    "/api/strategies/batch-backtest/stream",  # batch 流式
)

# ── 退役完整性三形制（tasks.md 任务 1 分拣形制 B/C）──
RETIRED_SYMBOLS = (
    # 形制 B：符号 import/调用
    "_VerifyProcessor", "compute_verify_star", "_threshold_star",
    "_update_strategy_verify_fields", "update_strategy_verify_stats",
    "verify_strategies",
    # 形制 C：字面
    "VerifySession", "verify_session",
)
REMOVED_SCHEMA_CLASSES = (
    "VerifyDateRange", "VerifyRequest", "VerifyResultItem",
    "VerifyPeriodSummary", "VerifySummaryItem", "VerifyResponse",
    "VerifyHistoryItem", "VerifyHistoryResponse",
    "VerifySessionResponse", "VerifySessionListResponse",
)
# ORM 停用保留豁免（契约明文：删类需迁移件且与存量库 create_all 漂移）
ORM_RETAINED_FILE = Path("core/persistence/orm/models.py")


class TestVerifyEndpointsRetired:
    """C1-1：五端点退役后不可达（完成标准①）。"""

    @pytest.mark.parametrize(("method", "path"), RETIRED_ENDPOINTS)
    def test_retired_endpoint_unreachable(
        self, api_client: TestClient, method: str, path: str,
    ) -> None:
        """四端点 404；POST /verify 撞 {strategy_id} 动态段为 405——
        两者同为 verify 路由不存在的运行时证明。"""
        if method == "POST":
            resp = api_client.post(path, json={})
        else:
            resp = api_client.get(path)
        assert resp.status_code in (404, 405), (
            f"{method} {path} 退役端点意外可达: {resp.status_code}")

    def test_openapi_paths_free_of_verify(self, api_client: TestClient) -> None:
        """OpenAPI 路由表零 verify 路径——路由未注册的静态证明
        （区别于「路由在但 handler 返回 404」）。"""
        paths = api_client.app.openapi()["paths"]
        verify_paths = [p for p in paths if "verify" in p]
        assert verify_paths == []


class TestSiblingRoutesMounted:
    """C1-4：四兄弟路由 import 链清洁（Verify 族 import 清理后
    装配正常——模块可导入+端点在路由表）。"""

    def test_four_sibling_modules_importable(self) -> None:
        for mod in SIBLING_MODULES:
            assert importlib.import_module(mod) is not None

    def test_sibling_endpoints_mounted_in_openapi(
        self, api_client: TestClient,
    ) -> None:
        paths = api_client.app.openapi()["paths"]
        missing = [p for p in SIBLING_MOUNTED_PATHS if p not in paths]
        assert missing == []


class TestStarColumnsFrozen:
    """C1-2：星级五列存量值冻结快照（Q1 选项 A，完成标准②）。

    退役前唯一写入者=verify 端点链路（compute_verify_star 回写）；
    退役后除 revalidate 生命周期重置（lifecycle 语义，另由
    TestStrategyRevalidate 承载）外全工程无写入者——本锚用普通
    操作族（读/更新/回测/批回测）验证运行时零触碰。
    """

    @staticmethod
    def _create_strategy(api_client: TestClient) -> str:
        resp = api_client.post("/api/strategies", json={
            "name": "FrozenStar", "dna": make_dna().to_dict(),
            "symbol": "BTCUSDT", "timeframe": "4h",
            "source": "manual", "tags": "", "notes": "",
        })
        assert resp.status_code == 201, resp.text[:300]
        return resp.json()["strategy_id"]

    @staticmethod
    def _write_seed_star_row(api_client: TestClient, sid: str) -> None:
        """直 SQL 写五列非空值——模拟退役前存量星级数据。"""
        conn = sqlite3.connect(str(api_client.app.state.db_path))
        try:
            conn.execute(
                "UPDATE strategy SET verify_star = ?, verify_avg_score = ?,"
                " verify_best_score = ?, last_verified_at = ?,"
                " verify_count = ? WHERE strategy_id = ?",
                (*_SEED_STAR_ROW, sid))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _read_star_row(api_client: TestClient, sid: str) -> tuple:
        conn = sqlite3.connect(str(api_client.app.state.db_path))
        try:
            return conn.execute(
                f"SELECT {', '.join(STAR_COLUMNS)} FROM strategy"
                " WHERE strategy_id = ?", (sid,)).fetchone()
        finally:
            conn.close()

    def test_star_columns_frozen_across_normal_ops(
        self, api_client: TestClient,
    ) -> None:
        """种子带非空星级值 → 普通操作族 → 五列逐列不变。"""
        sid = self._create_strategy(api_client)
        self._write_seed_star_row(api_client, sid)
        before = self._read_star_row(api_client, sid)
        assert before == _SEED_STAR_ROW  # 种子自证（防 UPDATE 未生效）

        # 读面（列表/详情——前端星标排序/展示消费面）
        assert api_client.get("/api/strategies").status_code == 200
        detail = api_client.get(f"/api/strategies/{sid}")
        assert detail.status_code == 200
        # 响应读面五列原样呈现（冻结快照对调用方可见）
        body = detail.json()
        assert (body["verify_star"], body["verify_avg_score"],
                body["verify_best_score"], body["verify_count"]) == (
            4, 71.5, 88.25, 3)

        # 更新面（PUT 白名单通道——五列不在 StrategyUpdate schema）
        put = api_client.put(f"/api/strategies/{sid}",
                             json={"name": "FrozenStarRenamed"})
        assert put.status_code == 200, put.text[:300]

        # 回测面（旧 verify 体系的写入语义面——退役后回测不回写星级）
        with patch("MyQuant.api.routes.strategies.backtest._bt_engine_mod") as bt_mod, \
                patch("MyQuant.api.routes.strategies.backtest.score_strategy") as mock_score:
            mock_result = MagicMock()
            mock_result.total_return = 0.25
            mock_result.sharpe_ratio = 1.8
            mock_result.max_drawdown = -0.12
            mock_result.win_rate = 0.62
            mock_result.total_trades = 150
            mock_result.equity_curve = MagicMock()
            mock_result.trades_df = None
            mock_result.total_funding_cost = 0.0
            mock_result.funding = {
                "mode": "perpetual", "total_cost": 12.5,
                "total_received": 0.0,
                "coverage": {"status": "not_loaded", "covered": None,
                             "gaps": []},
                "caliber_note": "回测为逐 bar 连续折算（8h 口径差披露）"}
            mock_result.liquidated = False
            mock_result.metrics_dict = {
                "benchmark_return": 0.15, "excess_return": 0.10,
                "alpha": 0.05, "market_annual_return": 0.20,
                "profit_factor": 1.6,
            }
            bt_mod.BacktestEngine.return_value.run.return_value = mock_result
            mock_score.return_value = {
                "total_score": 82.1,
                "dimension_scores": {"return": 90, "risk": 80},
                "template_name": "profit_first", "threshold": 75.0,
            }
            resp = api_client.post("/api/strategies/backtest", json={
                "strategy_id": sid, "dataset_id": "BTCUSDT_4h",
                "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
            })
            assert resp.status_code == 200, resp.text[:300]

        # 批回测流式面（batch 共享设施——结果落库不触碰星级）
        with patch("core.backtest.engine.BacktestEngine") as engine_cls, \
                patch("core.data.mtf_loader.load_and_prepare_df",
                      return_value=make_ohlcv(50, "4h")):
            engine_cls.return_value.batch_run.return_value = [
                _stream_engine_stub()]
            resp = api_client.post(
                "/api/strategies/batch-backtest/stream",
                json={"strategy_ids": [sid],
                      "data_ranges": [{"start": "2024-01-01",
                                       "end": "2024-06-01"}]})
            assert resp.status_code == 200, resp.text[:300]
            session_id = resp.json()["session_id"]
            assert session_id.startswith("batch_")
            # 等待后台完成（结果落库——unregister 在 finally 之后可见）
            from core.persistence.repository.backtest_result import (
                list_backtest_results)
            deadline = time.monotonic() + 15.0
            rows = []
            while time.monotonic() < deadline:
                rows = [r for r in list_backtest_results(
                    api_client.app.state.db_path,
                    run_source="batch_backtest", limit=5)
                    if r.get("strategy_id") == sid]
                if rows:
                    break
                time.sleep(0.05)
            assert rows, "后台批回测未在时限内落库"

        # 冻结断言：五列逐列与种子相等（sqlite 直读对照）
        assert self._read_star_row(api_client, sid) == _SEED_STAR_ROW

    def test_new_strategy_star_columns_default(
        self, api_client: TestClient,
    ) -> None:
        """边界：新建策略五列缺省（count=0/四列 None）——冻结快照
        只涉存量值，创建面不再种星级。"""
        sid = self._create_strategy(api_client)
        row = self._read_star_row(api_client, sid)
        assert row == (None, None, None, None, 0)
        body = api_client.get(f"/api/strategies/{sid}").json()
        assert body["verify_count"] == 0
        assert body["verify_star"] is None
        assert body["verify_avg_score"] is None
        assert body["verify_best_score"] is None
        assert body["last_verified_at"] is None


class TestVerifySessionArchived:
    """C1-3：VerifySession ORM 停用保留 + 表数据留档不删（完成标准⑥）。"""

    def test_orm_retained_importable_with_retired_note(self) -> None:
        """ORM 类保留停用（零迁移路线）——可导入且表名在册。"""
        from core.persistence.orm.models import VerifySession
        assert VerifySession.__tablename__ == "verify_session"

    def test_verify_session_table_queryable(
        self, api_client: TestClient,
    ) -> None:
        """表结构随建库在场、可查询（测试库新建零行——数据留档的
        存量库对照属部署期验证，运行时锚=表结构不消失+读写通道在）。"""
        conn = sqlite3.connect(str(api_client.app.state.db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM verify_session").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_verify_session_repository_removed(self) -> None:
        """仓储模块 core/persistence/repository/verify_session.py 已删。"""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("core.persistence.repository.verify_session")


class TestRetiredSymbolsAbsent:
    """退役完整性·运行时锚：模块/schema 属性/re-export 三面不存在。"""

    def test_verify_route_module_removed(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("MyQuant.api.routes.strategies.verify")

    def test_schemas_verify_family_removed(self) -> None:
        """Verify 族十类删除；BacktestDateRange 保留（去冠名——
        batch 请求在用）。"""
        import api.schemas as schemas
        leaked = [n for n in REMOVED_SCHEMA_CLASSES if hasattr(schemas, n)]
        assert leaked == []
        assert hasattr(schemas, "BacktestDateRange")

    def test_strategies_pkg_no_verify_reexports(self) -> None:
        """strategies 包 __init__ 的 re-export 三处已清。"""
        import MyQuant.api.routes.strategies as pkg
        for name in ("compute_verify_star", "_threshold_star",
                     "_update_strategy_verify_fields"):
            assert not hasattr(pkg, name), name


class TestRetirementSourceScan:
    """退役完整性·源文本锚：api/ core/ 代码级三形制零残留。

    tokenize 剔除 COMMENT/STRING 后扫描——docstring/注释中的历史
    提及不触发（不可执行）；代码级引用（import/调用/类型注解）零
    容忍。形制 A 路由 URL 是字符串字面量，由 C1-1 OpenAPI 锚承载。
    """

    @staticmethod
    def _code_only_tokens(text: str) -> str:
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
        return " ".join(out)

    def test_live_code_free_of_verify_references(self) -> None:
        root = Path(__file__).resolve().parents[2]
        hits: list[tuple[str, str]] = []
        scanned = 0
        for sub in ("api", "core"):
            for py in (root / sub).rglob("*.py"):
                rel = py.relative_to(root)
                if rel == ORM_RETAINED_FILE:
                    continue  # VerifySession ORM 停用保留（契约内豁免）
                scanned += 1
                code = self._code_only_tokens(
                    py.read_text(encoding="utf-8"))
                for sym in RETIRED_SYMBOLS:
                    if sym in code:
                        hits.append((str(rel), sym))
        assert scanned > 100, "扫描面异常收缩（api+core 应有数百文件）"
        assert hits == []


def _stream_engine_stub() -> SimpleNamespace:
    """BacktestEngine.batch_run 结果替身（零交易路径，
    形制同 test_batch_backtest_feature._fake_engine_result）。"""
    return SimpleNamespace(
        equity_curve=pd.Series([100000.0, 100050.0, 100100.0]),
        total_trades=0, win_rate=None, total_return=0.05,
        liquidated=False, total_funding_cost=0.0, funding=None,
        trades_df=None,
        metrics_dict={"sharpe_ratio": 0.1, "max_drawdown": 0.01,
                      "win_rate": None, "profit_factor": 0.0},
    )


class TestBatchStreamSequenceComplete:
    """C1-7：batch 流式事件序列完整（delta-3 Scenario——verify 退役
    后流式比对面=batch：N 次 progress 后恰一次 complete/error）。"""

    def test_progress_runs_then_single_complete(
        self, api_client: TestClient, monkeypatch,
    ) -> None:
        """两区间=两步：progress×2（total 声明一致、current 递增）后
        恰一次 complete 收尾，无丢序无重复。"""
        from api import realtime
        from core.persistence.repository.strategy import save_strategy

        save_strategy(
            api_client.app.state.db_path, strategy_id="s-seq",
            dna_json=make_dna().to_json(), symbol="BTCUSDT",
            timeframe="4h", name="SeqProbe",
        )
        events: list[tuple[str, dict]] = []

        def _record(channel, room, event, payload) -> None:
            events.append((event, payload))

        monkeypatch.setattr(realtime, "broadcast", _record)
        with patch("core.backtest.engine.BacktestEngine") as engine_cls, \
                patch("core.data.mtf_loader.load_and_prepare_df",
                      return_value=make_ohlcv(50, "4h")):
            engine_cls.return_value.batch_run.return_value = [
                _stream_engine_stub()]
            resp = api_client.post(
                "/api/strategies/batch-backtest/stream",
                json={"strategy_ids": ["s-seq"],
                      "data_ranges": [
                          {"start": "2024-01-01", "end": "2024-03-01"},
                          {"start": "2024-04-01", "end": "2024-06-01"},
                      ]})
            assert resp.status_code == 200, resp.text[:300]
            session_id = resp.json()["session_id"]
            assert session_id.startswith("batch_")
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if events and events[-1][0] in ("complete", "error"):
                    break
                time.sleep(0.05)

        names = [e for e, _ in events]
        assert names, "流式通道零事件（broadcast 未被调用）"
        assert names[-1] == "complete", f"序列未以 complete 收尾: {names}"
        assert names.count("complete") == 1, f"complete 不止一次: {names}"
        progress_payloads = [p for e, p in events if e == "progress"]
        assert len(progress_payloads) == 2, names
        # progress.total 声明的步数与实际 progress 次数一致
        assert all(p["total"] == 2 for p in progress_payloads)
        assert [p["current"] for p in progress_payloads] == [1, 2]
