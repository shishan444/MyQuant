"""ohlcv tail 参数契约测试（kline-common-layer 组 5.3，spec kline-data-supply 4 Scenario）。

固定数据集 30 根 4h bar；兼容锚=不传 tail 时响应与现行 head 语义逐行一致
（start/end/limit 各组合对 pandas 手算期），即「改造前基线快照」的可复现对照源。
"""
from __future__ import annotations

import io

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _csv_30_bars() -> bytes:
    """30 根 2024-01-01 起 4h 递增 bar（close=i*10 便于断言行序）。"""
    import datetime as dt

    rows = ["timestamp,open,high,low,close,volume"]
    t0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(30):
        ts = (t0 + dt.timedelta(hours=4 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(f"{ts},{i},{i},{i},{i},{i}")
    return "\n".join(rows).encode()


@pytest.fixture()
def dataset_id(api_client: TestClient) -> str:
    r = api_client.post(
        "/api/data/import",
        files={"file": ("KLTAIL_4h.csv", io.BytesIO(_csv_30_bars()), "text/csv")},
        data={"mode": "replace", "symbol": "KLTAIL", "interval": "4h"},
    )
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def _rows(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestTailBasic:
    """Scenario: tail 基本语义"""

    def test_tail_24_returns_last_24_ascending(self, api_client, dataset_id):
        rows = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"tail": 24}))
        assert len(rows) == 24
        # 升序最后 24 根：close=6..29
        closes = [r["close"] for r in rows]
        assert closes == list(range(6, 30))
        assert rows[0]["timestamp"].startswith("2024-01-02")
        # 与手工取尾部 24 行逐行一致（对照锚）
        full = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv"))
        assert rows == full[-24:]

    def test_tail_insufficient_returns_all(self, api_client, dataset_id):
        rows = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"tail": 50000}))
        assert len(rows) == 30  # 不足 N 全给非错误

    def test_tail_with_end_anchored_window(self, api_client, dataset_id):
        # end 锚定第 20 根（index 19，2024-01-04T04:00Z）→ 从锚定行往回 5 根：close=15..19
        rows = _rows(
            api_client.get(
                f"/api/data/datasets/{dataset_id}/ohlcv",
                params={"tail": 5, "end": "2024-01-04T04:00:00Z"},
            )
        )
        assert [r["close"] for r in rows] == [15, 16, 17, 18, 19]


class TestTailConflict:
    """Scenario: tail 与 start / limit 互斥"""

    def test_tail_start_422(self, api_client, dataset_id):
        r = api_client.get(
            f"/api/data/datasets/{dataset_id}/ohlcv",
            params={"tail": 10, "start": "2024-01-01T00:00:00Z"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DATA_PARAM_CONFLICT"

    def test_tail_limit_422(self, api_client, dataset_id):
        r = api_client.get(
            f"/api/data/datasets/{dataset_id}/ohlcv",
            params={"tail": 10, "limit": 100},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DATA_PARAM_CONFLICT"

    def test_conflict_checked_before_query_404_dataset(self, api_client):
        # 互斥校验先于查询：不存在的数据集+互斥组合同给 422（spec：不执行查询）
        r = api_client.get(
            "/api/data/datasets/nonexistent/ohlcv",
            params={"tail": 10, "start": "2024-01-01T00:00:00Z"},
        )
        assert r.status_code == 422


class TestCurrentContractUnchanged:
    """Scenario: 现行契约不变（不传 tail 时 head 语义逐字节保留——改造前基线对照）"""

    def test_no_params_full_head_semantics(self, api_client, dataset_id):
        rows = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv"))
        assert len(rows) == 30
        assert [r["close"] for r in rows] == list(range(30))

    def test_limit_explicit_head(self, api_client, dataset_id):
        rows = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"limit": 5}))
        assert [r["close"] for r in rows] == [0, 1, 2, 3, 4]

    def test_start_end_window(self, api_client, dataset_id):
        rows = _rows(
            api_client.get(
                f"/api/data/datasets/{dataset_id}/ohlcv",
                params={"start": "2024-01-01T08:00:00Z", "end": "2024-01-01T16:00:00Z"},
            )
        )
        assert [r["close"] for r in rows] == [2, 3, 4]

    def test_dataset_404_along_status_quo(self, api_client):
        r = api_client.get("/api/data/datasets/nonexistent/ohlcv")
        assert r.status_code == 404

    def test_response_structure_shape(self, api_client, dataset_id):
        body = api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"tail": 3}).json()
        assert set(body.keys()) == {"dataset_id", "data"}
        assert set(body["data"][0].keys()) >= {"timestamp", "open", "high", "low", "close", "volume"}
