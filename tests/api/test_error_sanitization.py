"""异常消息脱敏特征网（CARD-07 修复锚定，修复期安全网）。

锚定 api/routes/strategies/_common.py::_safe_error_message 及其接入面：
- FNF → "数据文件缺失: <basename>"（剥离绝对路径，不泄露文件系统结构）
- FNF 无 filename → "数据文件缺失"
- 未知异常 → 恒 "内部错误"，原文经 logger.warning 保留（观测边界内可溯源）
- batch 处理器组级 FNF 端到端：error 条目为脱敏文案
- compare 坏 DNA 条目：error 为脱敏文案、其余条目正常（隔离语义不变）
- trading 创建任务非法 dna_json：400 detail 恒固定文案（另见
  test_trading_routes_extra.py 收紧断言）
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna, make_ohlcv


class TestSafeErrorMessageHelper:
    def _helper(self):
        from api.routes.strategies._common import _safe_error_message
        return _safe_error_message

    def test_fnf_strips_absolute_path(self) -> None:
        """FNF → 数据文件缺失 + basename（绝对路径不外泄）。"""
        exc = FileNotFoundError(2, "No such file", "/Users/secret/home/data/BTCUSDT_4h.parquet")
        msg = self._helper()(exc)
        assert msg == "数据文件缺失: BTCUSDT_4h.parquet"
        assert "/Users" not in msg and "secret" not in msg

    def test_fnf_without_filename(self) -> None:
        exc = FileNotFoundError(2, "No such file or directory")
        assert self._helper()(exc) == "数据文件缺失"

    def test_unknown_exception_generic_message_logged(
        self, caplog,
    ) -> None:
        """未知异常 → 恒「内部错误」，原文进 warning 日志可溯源。"""
        exc = RuntimeError("KeyError secret internal path /etc/passwd")
        with caplog.at_level("WARNING", logger="api.routes.strategies._common"):
            msg = self._helper()(exc)
        assert msg == "内部错误"
        assert "secret" not in msg
        assert any("secret internal" in r.getMessage() for r in caplog.records)

    def test_value_error_is_unknown_branch(self) -> None:
        """ValueError 无专门映射 → 走未知分支（内部错误）。"""
        assert self._helper()(ValueError("boom detail")) == "内部错误"


class _FakeProcessorMixin:
    """batch 处理器直调脚手架（与 test_batch_backtest_feature 同形制）。"""

    @staticmethod
    def _make_processor(db_path: Path, tmp_path: Path):
        from api.routes.strategies.batch import _BatchBacktestProcessor
        from api.schemas import BatchBacktestRequest, VerifyDateRange
        from core.persistence.db_ext import save_strategy
        save_strategy(
            db_path, strategy_id="s-1", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="S1",
        )
        payload = BatchBacktestRequest(
            strategy_ids=["s-1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
        )
        return _BatchBacktestProcessor(payload, db_path, tmp_path)


class TestBatchFnfSanitized(_FakeProcessorMixin):
    def test_group_fnf_error_sanitized(self, tmp_path: Path) -> None:
        """组级 FNF → error 条目为「数据文件缺失: 文件名」，无路径。"""
        from core.persistence.db_ext import init_db_ext
        db_path = tmp_path / "b.db"
        init_db_ext(db_path)
        processor = self._make_processor(db_path, tmp_path)
        processor.init()

        def _fnf(*a, **kw):
            raise FileNotFoundError(2, "No such file",
                                    f"{tmp_path}/data/BTCUSDT_4h.parquet")

        processor._load_df = _fnf
        progress = processor.process_step(0)
        assert all(r["error"] == "数据文件缺失: BTCUSDT_4h.parquet"
                   for r in progress["batch_results"])
        assert all(r.error == "数据文件缺失: BTCUSDT_4h.parquet"
                   for r in processor.all_results)


class TestCompareErrorSanitized:
    def test_corrupt_dna_error_sanitized_others_ok(
        self, api_client: TestClient,
    ) -> None:
        """坏 DNA 条目 error 为脱敏文案，正常条目照常（隔离语义）。"""
        from core.persistence.db_ext import save_strategy
        make_ohlcv(60, "4h").to_parquet(
            api_client.app.state.data_dir / "BTCUSDT_4h.parquet",
        )
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="bad-dna", dna_json="{corrupt",
            symbol="BTCUSDT", timeframe="4h", name="Broken",
        )
        save_strategy(
            db_path, strategy_id="ok-dna", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="Fine",
        )
        response = api_client.post(
            "/api/strategies/compare",
            json={"strategy_ids": ["bad-dna", "ok-dna"], "dataset_id": "BTCUSDT_4h"},
        )
        assert response.status_code == 200
        by_id = {r["strategy_id"]: r for r in response.json()["results"]}
        assert by_id["bad-dna"]["error"] == "内部错误"
        assert not by_id["ok-dna"].get("error")
