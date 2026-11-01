"""ohlcv tail 参数契约测试（kline-common-layer 组 5.3，spec kline-data-supply 4 Scenario）。

契约端点=GET /api/data/ohlcv/{symbol}/{timeframe}（symbol 端点，spec Requirement 原文）；
CARD-05 前实现误落 dataset 端点已回退原状——dataset 端点行为面回归由
tests/api/test_api.py 存量用例（404/取数）承接，本文件不重复。

数据经 parquet 直放置（symbol 端点径直 find_parquet 寻址，不走 import）：
KLTAIL_4h=30 根、KLTAILBIG_4h=1200 根，2024-01-01 起 4h 递增（close=i 便于断言行序）。
兼容锚=不传 tail 时 head 语义（缺省 limit=50000 不足即全给）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

BASE = "/api/data/ohlcv/KLTAIL/4h"
BIG = "/api/data/ohlcv/KLTAILBIG/4h"


def _bars_df(n: int) -> pd.DataFrame:
    """n 根 2024-01-01 起 4h 递增 bar（close=i 便于断言行序）。"""
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=4 * i) for i in range(n)],
        name="timestamp",
    )
    return pd.DataFrame(
        {"open": range(n), "high": range(n), "low": range(n), "close": range(n), "volume": [1] * n},
        index=idx,
    )


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """覆写 conftest：30 根+1200 根两份 parquet（conftest 形制——本地覆写可承载自定义数据）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _bars_df(30).to_parquet(data_dir / "KLTAIL_4h.parquet")
    _bars_df(1200).to_parquet(data_dir / "KLTAILBIG_4h.parquet")
    return data_dir


def _rows(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestTailBasic:
    """Scenario: tail 基本语义"""

    def test_tail_24_returns_last_24_ascending(self, api_client):
        rows = _rows(api_client.get(BASE, params={"tail": 24}))
        assert len(rows) == 24
        # 升序最后 24 根：close=6..29
        assert [r["close"] for r in rows] == list(range(6, 30))
        assert rows[0]["timestamp"].startswith("2024-01-02")
        # 与全量末 24 行逐行一致（对照锚）
        full = _rows(api_client.get(BASE))
        assert rows == full[-24:]

    def test_tail_insufficient_returns_all_and_upper_bound_valid(self, api_client):
        # tail=50000=上界值本身合法（le=50000 含边界）；30 行不足 N 全给非错误
        rows = _rows(api_client.get(BASE, params={"tail": 50000}))
        assert len(rows) == 30

    def test_tail_with_end_anchored_window(self, api_client):
        # end 锚定第 20 根（index 19，2024-01-04T04:00Z）→ 从锚定行往回 5 根：close=15..19
        rows = _rows(api_client.get(BASE, params={"tail": 5, "end": "2024-01-04T04:00:00Z"}))
        assert [r["close"] for r in rows] == [15, 16, 17, 18, 19]

    def test_tail_1_min_valid_returns_last_bar(self, api_client):
        # tail 最小有效值 1：从最新一根往回取 1 根=最后一根（ge=1 下界）
        rows = _rows(api_client.get(BASE, params={"tail": 1}))
        assert len(rows) == 1
        assert rows[0]["close"] == 29
        assert rows[0]["timestamp"].startswith("2024-01-05")


class TestTailConflict:
    """Scenario: tail 与 start / limit 互斥"""

    def test_tail_start_422(self, api_client):
        r = api_client.get(BASE, params={"tail": 10, "start": "2024-01-01T00:00:00Z"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DATA_PARAM_CONFLICT"

    def test_tail_limit_422(self, api_client):
        r = api_client.get(BASE, params={"tail": 10, "limit": 100})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DATA_PARAM_CONFLICT"

    def test_conflict_checked_before_parquet_lookup_404_symbol(self, api_client):
        # 互斥校验先于查询（find_parquet 前置校验）：不存在 symbol+互斥组合同给 422 而非 404
        r = api_client.get(
            "/api/data/ohlcv/NOSUCH/4h",
            params={"tail": 10, "start": "2024-01-01T00:00:00Z"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DATA_PARAM_CONFLICT"


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
    def test_tail_out_of_domain_422_query_validation(self, api_client, tail):
        r = api_client.get(BASE, params={"tail": tail})
        assert r.status_code == 422
        # 沿 Query 校验层（数组形制），非业务互斥码
        assert isinstance(r.json()["detail"], list)


class TestCurrentContractUnchanged:
    """Scenario: 现行契约不变（不传 tail 时 head 语义保留，limit 缺省 50000 不足即全给）"""

    def test_no_params_full_head_semantics(self, api_client):
        rows = _rows(api_client.get(BASE))
        assert len(rows) == 30
        assert [r["close"] for r in rows] == list(range(30))

    def test_limit_explicit_head(self, api_client):
        rows = _rows(api_client.get(BASE, params={"limit": 5}))
        assert [r["close"] for r in rows] == [0, 1, 2, 3, 4]

    def test_start_end_window(self, api_client):
        rows = _rows(
            api_client.get(
                BASE,
                params={"start": "2024-01-01T08:00:00Z", "end": "2024-01-01T16:00:00Z"},
            )
        )
        assert [r["close"] for r in rows] == [2, 3, 4]

    def test_symbol_404_along_status_quo(self, api_client):
        r = api_client.get("/api/data/ohlcv/NOSUCH/4h")
        assert r.status_code == 404
        assert r.json()["detail"] == "No data for NOSUCH/4h"

    def test_response_structure_shape(self, api_client):
        body = api_client.get(BASE, params={"tail": 3}).json()
        assert set(body.keys()) == {"dataset_id", "data"}
        assert body["dataset_id"] == "KLTAIL_4h"
        assert set(body["data"][0].keys()) >= {"timestamp", "open", "high", "low", "close", "volume"}

    def test_no_params_default_limit_50000_on_large_dataset(self, api_client):
        """现行契约缺省值锚：limit 签名 Query(50000)→Query(None)+函数体回填 50000。

        1200 行数据集无参请求恰返回 1200 行——若回填值漂移（如误为 1000）则截为
        1000 行，本锚即红；同数据集 tail=50000 不足全给（大数据集侧的不足语义）。
        """
        rows = _rows(api_client.get(BIG))
        assert len(rows) == 1200
        assert [row["close"] for row in rows] == list(range(1200))

        tail_rows = _rows(api_client.get(BIG, params={"tail": 50000}))
        assert len(tail_rows) == 1200
        assert [row["close"] for row in tail_rows] == list(range(1200))
