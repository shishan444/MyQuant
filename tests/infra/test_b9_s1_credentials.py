"""B9.2 S1 凭证回显修复——脱敏往返（design.md B9 节冻结方案）。

GET 凭证键全掩码 "***"（CARD-06 后不泄露首尾片段，未配置回 ""）；
PUT 含 *** 的凭证值=未修改回显（哨兵），不覆盖已存值。前端零改动
（Settings 表单显示 masked 即「已配置」）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]

CREDS = ("binance_api_key", "binance_secret_key")


@pytest.fixture
def client(tmp_path: Path):
    from api.app import create_app
    from core.persistence.db_ext import init_db_ext

    init_db_ext(tmp_path / "t.db")
    app = create_app(db_path=tmp_path / "t.db", data_dir=tmp_path)
    with TestClient(app) as c:
        yield c


class TestCredentialMasking:
    def test_masked_roundtrip(self, client):
        """配置→GET masked→PUT 哨兵→GET 仍 masked→存储值未损。"""
        # 1) 配置明文
        r = client.put("/api/config", json={
            "binance_api_key": "AbCdEfGhIjKlMnOp",
            "binance_secret_key": "SeCrEtKeY12345678",
        })
        assert r.status_code == 200

        # 2) GET 全掩码（CARD-06 后恒 ***，不泄露首尾片段）
        cfg = client.get("/api/config").json()
        assert cfg["binance_api_key"] == "***"
        assert cfg["binance_secret_key"] == "***"

        # 3) PUT 哨兵（表单回显值原样提交，含 ***）→ 不覆盖已存值
        r = client.put("/api/config", json={
            "binance_api_key": "***",
            "binance_secret_key": "***",
        })
        assert r.status_code == 200

        # 4) GET 仍全掩码 + 存储值未损（经底层文件直查）
        cfg = client.get("/api/config").json()
        assert cfg["binance_api_key"] == "***"
        import json as _json
        db_dir = client.app.state.db_path.parent
        stored = _json.loads((db_dir / "config.json").read_text())
        assert stored["binance_api_key"] == "AbCdEfGhIjKlMnOp"
        assert stored["binance_secret_key"] == "SeCrEtKeY12345678"

    def test_unconfigured_creds_return_empty(self, client):
        cfg = client.get("/api/config").json()
        assert cfg["binance_api_key"] == ""
        assert cfg["binance_secret_key"] == ""

    def test_short_credential_masks_fully(self, client):
        """短于掩码窗的凭证不泄露（全掩或近似掩）。"""
        client.put("/api/config", json={"binance_api_key": "short"})
        cfg = client.get("/api/config").json()
        assert "short" not in cfg["binance_api_key"]
        assert cfg["binance_api_key"]

    def test_new_plaintext_overrides(self, client):
        """真实新值（不含 ***）正常覆盖（回显全掩码、落盘新值）。"""
        import json as _json2
        client.put("/api/config", json={"binance_api_key": "AAAA1111BBBB"})
        client.put("/api/config", json={"binance_api_key": "ZZZZ9999YYYY"})
        cfg = client.get("/api/config").json()
        assert cfg["binance_api_key"] == "***"
        db_dir = client.app.state.db_path.parent
        stored = _json2.loads((db_dir / "config.json").read_text())
        assert stored["binance_api_key"] == "ZZZZ9999YYYY"

    def test_non_credentials_passthrough(self, client):
        """非凭证键不受掩码影响。"""
        client.put("/api/config", json={"timezone": "UTC+9", "init_cash": 50000})
        cfg = client.get("/api/config").json()
        assert cfg["timezone"] == "UTC+9"
        assert cfg["init_cash"] == 50000
