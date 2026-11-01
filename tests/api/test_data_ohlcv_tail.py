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


def _csv_n_bars(n: int) -> bytes:
    """n 根 4h 递增 bar（limit 缺省 1000 截断语义需要 >1000 行数据集）。"""
    import datetime as dt

    rows = ["timestamp,open,high,low,close,volume"]
    t0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(n):
        ts = (t0 + dt.timedelta(hours=4 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(f"{ts},{i},{i},{i},{i},1")
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

    def test_tail_1_min_valid_returns_last_bar(self, api_client, dataset_id):
        # tail 最小有效值 1：从最新一根往回取 1 根=最后一根（ge=1 下界）
        rows = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"tail": 1}))
        assert len(rows) == 1
        assert rows[0]["close"] == 29
        assert rows[0]["timestamp"].startswith("2024-01-05")


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


class TestTailBoundaries:
    """Requirement 边界：tail 正整数、上限 50000（design §7 tail=0/负/越界→422 沿 Query 校验）。

    Query 校验层（FastAPI）与业务互斥层（DATA_PARAM_CONFLICT）是两层 422：
    校验层 detail 为数组形制，业务层 detail 为 {code,...}——断言形制以证明走的是校验层。
    """

    @pytest.mark.parametrize(
        "tail",
        [
            pytest.param(0, id="tail=0 非正整数"),
            pytest.param(-3, id="tail=-3 负数"),
            pytest.param(50001, id="tail=50001 越上限"),
        ],
    )
    def test_tail_out_of_domain_422_query_validation(self, api_client, dataset_id, tail):
        r = api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"tail": tail})
        assert r.status_code == 422
        # 沿 Query 校验层（数组形制），非业务互斥码
        assert isinstance(r.json()["detail"], list)

    def test_tail_50000_upper_bound_accepted(self, api_client, dataset_id):
        # 上界值 50000 本身合法（le=50000 含边界；不足 N 全给由 30 行数据集承接）
        rows = _rows(api_client.get(f"/api/data/datasets/{dataset_id}/ohlcv", params={"tail": 50000}))
        assert len(rows) == 30


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

    def test_no_params_default_limit_1000_head_on_large_dataset(self, api_client):
        """现行契约不变的核心风险点：limit 缺省 1000 的 head 截断。

        改造把 limit 签名从 Query(1000) 改为 Query(None)+函数体回填 1000——
        30 行小数据集（行数<1000）无法暴露缺省值漂移；1200 行数据集无参请求
        必须恰返回前 1000 行（close=0..999）才能证明缺省语义逐字节未变。
        """
        r = api_client.post(
            "/api/data/import",
            files={"file": ("KLTAIL_BIG_4h.csv", io.BytesIO(_csv_n_bars(1200)), "text/csv")},
            data={"mode": "replace", "symbol": "KLTAILBIG", "interval": "4h"},
        )
        assert r.status_code == 200, r.text
        ds = r.json()["dataset_id"]

        rows = _rows(api_client.get(f"/api/data/datasets/{ds}/ohlcv"))
        assert len(rows) == 1000
        assert [row["close"] for row in rows] == list(range(1000))

        # 同数据集 tail 不足全给：1200 行全量升序（大数据集侧的不足语义）
        tail_rows = _rows(api_client.get(f"/api/data/datasets/{ds}/ohlcv", params={"tail": 50000}))
        assert len(tail_rows) == 1200
        assert [row["close"] for row in tail_rows] == list(range(1200))
