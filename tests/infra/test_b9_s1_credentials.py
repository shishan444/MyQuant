"""config 凭据链测试（fix-audit-disclosure-security S-01 拆链改写）。

B9.2 S1 掩码语义已随凭据通道退役（凭据唯一通道=data/credentials.json）；
本文件承载拆链后端到端语义：GET/PUT 零凭据键 + lifespan 存量迁移真链路
（TestClient with 块真实走 app lifespan——config.json 预置明文 → 启动
迁移 → 凭据文件生成 + 物理删键）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]

CREDS = ("binance_api_key", "binance_secret_key")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from api.app import create_app
    from core.persistence.db_ext import init_db_ext

    monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE",
                       str(tmp_path / "creds.json"))
    init_db_ext(tmp_path / "t.db")
    app = create_app(db_path=tmp_path / "t.db", data_dir=tmp_path)
    with TestClient(app) as c:
        yield c


class TestConfigChannelCredentialFree:
    def test_get_has_no_credential_keys(self, client):
        cfg = client.get("/api/config").json()
        for k in (*CREDS, "binance_connected"):
            assert k not in cfg, f"拆链后 GET 不应回显 {k}"

    def test_put_credential_keys_ignored_no_persist(self, client):
        r = client.put("/api/config", json={
            "binance_api_key": "AbCdEfGhIjKlMnOp",
            "binance_secret_key": "SeCrEtKeY12345678",
        })
        assert r.status_code == 200
        for k in (*CREDS, "binance_connected"):
            assert k not in r.json()
        db_dir = client.app.state.db_path.parent
        stored = json.loads((db_dir / "config.json").read_text())
        for k in (*CREDS, "binance_connected"):
            assert k not in stored, f"凭据键 {k} 不得经 PUT 落盘"

    def test_non_credentials_passthrough(self, client):
        """非凭据键不受拆链影响。"""
        client.put("/api/config", json={"timezone": "UTC+9", "init_cash": 50000})
        cfg = client.get("/api/config").json()
        assert cfg["timezone"] == "UTC+9"
        assert cfg["init_cash"] == 50000


class TestLifespanMigrationE2E:
    def test_startup_migrates_legacy_plaintext(self, tmp_path, monkeypatch):
        """端到端：config.json 预置明文 → TestClient 启动（lifespan）→
        凭据文件生成（值迁移+权限）+ config.json 物理删键 + GET 零键。"""
        cred_path = tmp_path / "e2e-creds.json"
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(cred_path))
        from api.app import create_app
        from core.persistence.db_ext import init_db_ext

        db_path = tmp_path / "t.db"
        init_db_ext(db_path)
        (db_path.parent / "config.json").write_text(json.dumps({
            "binance_api_key": "LegacyKey" + "a" * 55,
            "binance_secret_key": "LegacySecret" + "b" * 54,
            "binance_connected": True,
            "language": "zh-CN",
        }), encoding="utf-8")

        app = create_app(db_path=db_path, data_dir=tmp_path)
        with TestClient(app) as c:
            cfg = c.get("/api/config").json()
            for k in (*CREDS, "binance_connected"):
                assert k not in cfg
            stored = json.loads(
                (db_path.parent / "config.json").read_text(encoding="utf-8"))
            for k in (*CREDS, "binance_connected"):
                assert k not in stored, f"lifespan 应物理删除 {k}"
            assert stored["language"] == "zh-CN", "普通配置逐字节保留"
        assert cred_path.exists(), "凭据文件应生成"
        import os
        import stat
        assert stat.S_IMODE(os.stat(cred_path).st_mode) == 0o600
        migrated = json.loads(cred_path.read_text(encoding="utf-8"))
        assert migrated["binance_api_key"].startswith("LegacyKey")
