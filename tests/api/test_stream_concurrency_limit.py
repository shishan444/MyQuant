"""流式端点并发槽位特征网（CARD-08 修复锚定，修复期安全网）。

锚定 api/stream_control.py 并发槽位与 verify/batch 两流式端点接入：
- 单元：acquire 4 次、第 5 次拒绝、release 归还、Bounded 双释放防护
- 端点：并发满 → 429 "Too many concurrent streams"；init 404 路径槽位
  先归还（紧随请求可再占）
- 取消注册表语义不变（test_stream_control_unit.py 已锚定，不重复）

注：槽位为模块级全局（跨 app 实例共享），_balance_slots fixture 在
每用例前后自愈任何泄漏（drain 后按实占数回填）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from api import stream_control
from tests.helpers.data_factory import make_dna, make_ohlcv


@pytest.fixture(autouse=True)
def _balance_slots():
    """用例前后将全局槽位恢复满额（自愈跨用例/后台任务泄漏）。

    按 _value 差额回填：满占状态下 drain 式自检拿不到占用数，
    只能读内部计数补差（测试基建可接受的实现细节引用）。
    """
    def _refill() -> None:
        missing = 4 - stream_control._STREAM_SLOTS._value
        for _ in range(missing):
            stream_control.release_stream_slot()

    _refill()
    yield
    _refill()


class TestSlotUnit:
    def test_acquire_four_then_fifth_rejected(self) -> None:
        """全局 4 槽：第 5 次 acquire 返回 False（非阻塞拒绝）。"""
        assert stream_control.acquire_stream_slot()
        assert stream_control.acquire_stream_slot()
        assert stream_control.acquire_stream_slot()
        assert stream_control.acquire_stream_slot()
        assert stream_control.acquire_stream_slot() is False

    def test_release_restores_capacity(self) -> None:
        stream_control.acquire_stream_slot()
        stream_control.release_stream_slot()
        # 归还后可再次占用满额 4 个
        for _ in range(4):
            assert stream_control.acquire_stream_slot()

    def test_bounded_over_release_raises(self) -> None:
        """Bounded 语义：满额（fixture 保证开局 4/4）再放 → ValueError。

        端点侧防重复归还的对应形态：占 N 放 N+1 的最后一次 release
        恰使计数超初始上限。Python BoundedSemaphore 对「他人持有时的
        补放」（未超上限）不抛——防的是超界不是严格配对。
        """
        with pytest.raises(ValueError, match="released too many times"):
            stream_control.release_stream_slot()


class TestStreamEndpoint429:
    def test_verify_stream_rejected_when_full(self, api_client: TestClient) -> None:
        """并发满：POST /verify/stream → 429 固定 detail。"""
        from core.persistence.repository.strategy import save_strategy
        save_strategy(
            api_client.app.state.db_path, strategy_id="s-cc",
            dna_json=make_dna().to_json(), symbol="BTCUSDT", timeframe="4h",
        )
        for _ in range(4):
            assert stream_control.acquire_stream_slot()
        response = api_client.post(
            "/api/strategies/verify/stream",
            json={"strategy_ids": ["s-cc"],
                  "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}]},
        )
        assert response.status_code == 429
        assert response.json()["detail"] == "Too many concurrent streams"

    def test_batch_stream_rejected_when_full(self, api_client: TestClient) -> None:
        from core.persistence.repository.strategy import save_strategy
        save_strategy(
            api_client.app.state.db_path, strategy_id="s-bc",
            dna_json=make_dna().to_json(), symbol="BTCUSDT", timeframe="4h",
        )
        for _ in range(4):
            assert stream_control.acquire_stream_slot()
        response = api_client.post(
            "/api/strategies/batch-backtest/stream",
            json={"strategy_ids": ["s-bc"],
                  "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}]},
        )
        assert response.status_code == 429
        assert response.json()["detail"] == "Too many concurrent streams"

    def test_verify_404_init_path_returns_slot(
        self, api_client: TestClient,
    ) -> None:
        """init 404 路径槽位先归还：紧随其后的请求可占用。"""
        from core.persistence.repository.strategy import save_strategy
        save_strategy(
            api_client.app.state.db_path, strategy_id="s-ok",
            dna_json=make_dna().to_json(), symbol="BTCUSDT", timeframe="4h",
        )
        # 预占 3 槽（剩 1）
        for _ in range(3):
            assert stream_control.acquire_stream_slot()
        # 无效策略：acquire 第 4 槽 → init 404 → 槽位归还
        rejected = api_client.post(
            "/api/strategies/verify/stream",
            json={"strategy_ids": ["ghost"],
                  "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}]},
        )
        assert rejected.status_code == 404
        # 第 4 槽已归还：有效请求可占（否则将 429）
        with patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")):
            accepted = api_client.post(
                "/api/strategies/verify/stream",
                json={"strategy_ids": ["s-ok"],
                      "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}]},
            )
        assert accepted.status_code == 200
        assert accepted.json()["session_id"]
