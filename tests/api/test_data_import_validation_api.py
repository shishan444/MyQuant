"""数据导入端点非法参数 400 契约（CARD-01/02/03 API 面）。

经真实路由装配（TestClient）验证：symbol/interval 白名单拒绝与
OHLCV 校验失败的 ValueError 在 /api/data/import 与 /import-batch
两端点转换为 HTTP 400，且数据不落盘。
"""

import pytest

pytestmark = [pytest.mark.integration]

from pathlib import Path

from fastapi.testclient import TestClient

GOOD_CSV = (
    "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
).encode()
BAD_OHLCV_CSV = (
    "timestamp,open,high,low,close,volume\n"
    "2024-01-01 00:00:00,100,101,102,100.5,10\n"  # low > min(open, close)
).encode()


def _data_dir(client: TestClient) -> Path:
    return Path(client.app.state.data_dir)


class TestImportEndpointValidation:
    """POST /api/data/import：非法参数 400 + 无落盘。"""

    def test_traversal_symbol_rejected_400(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("data.csv", GOOD_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "../../outside", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "symbol 非法" in resp.json()["detail"]
        assert list(_data_dir(api_client).glob("outside*")) == []

    def test_traversal_interval_rejected_400(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("data.csv", GOOD_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "BTCUSDT", "interval": "../../evil"},
        )
        assert resp.status_code == 400
        assert "interval 非法" in resp.json()["detail"]
        assert list(_data_dir(api_client).glob("BTCUSDT_..*")) == []

    def test_bad_ohlcv_rejected_400_no_write(self, api_client: TestClient) -> None:
        """坏 CSV（OHLCV 校验失败）→ 400 且数据不落盘。"""
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("BAD_4h.csv", BAD_OHLCV_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "BAD", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "OHLCV validation failed" in resp.json()["detail"]
        assert not (_data_dir(api_client) / "BAD_4h.parquet").exists()

    def test_valid_import_still_succeeds(self, api_client: TestClient) -> None:
        """合法导入不受守卫影响（防误伤回归锚）。"""
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("BTCUSDT-4h-2025-01.csv", GOOD_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "BTCUSDT", "interval": "4h"},
        )
        assert resp.status_code == 200
        assert resp.json()["dataset_id"] == "BTCUSDT_4h"
        assert resp.json()["timestamp_precision"] == "ms"  # BINANCE 探测面

    def test_generic_import_precision_null(self, api_client: TestClient) -> None:
        """GENERIC 导入响应 precision 为 null（CARD-13：不可探测不入伪值）。"""
        generic_csv = (
            "timestamp,open,high,low,close,volume\n"
            "2024-01-01 00:00:00,42000.0,42580.0,41850.0,42310.0,312.5\n"
            "2024-01-01 04:00:00,42310.0,43120.0,42100.0,42850.0,289.3\n"
        ).encode()
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("GEN-4h.csv", generic_csv, "text/csv")},
            data={"mode": "replace", "symbol": "GEN", "interval": "4h"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["timestamp_precision"] is None
        assert body["format_detected"] == "generic_ohlcv"


class TestImportBatchEndpointValidation:
    """POST /api/data/import-batch：非法参数 400 + 无落盘。"""

    def test_traversal_symbol_rejected_400(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/data/import-batch",
            files=[("files", ("data.csv", GOOD_CSV, "text/csv"))],
            data={"symbol": "../../outside", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "symbol 非法" in resp.json()["detail"]
        assert list(_data_dir(api_client).glob("outside*")) == []

    def test_unicode_digit_symbol_rejected_400(self, api_client: TestClient) -> None:
        """全角数字 symbol（Unicode 匹配陷阱）→ 400。"""
        resp = api_client.post(
            "/api/data/import-batch",
            files=[("files", ("data.csv", GOOD_CSV, "text/csv"))],
            data={"symbol": "１２３", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "symbol 非法" in resp.json()["detail"]
