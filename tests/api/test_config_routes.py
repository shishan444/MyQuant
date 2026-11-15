"""Config 路由特征网（修复期安全网，现状记录语义）。

覆盖 api/routes/config.py 的 GET/PUT 端点（fix-audit-disclosure-security
S-01 拆链后契约：GET/PUT 零凭据语义——凭据唯一通道=data/credentials.json，
掩码/哨兵语义已随通道退役）：
- GET 默认配置回显（无 config.json 时全默认值）
- GET 响应零凭据键（含迁移前存量窗口防明文回显）
- PUT 普通键更新并落盘
- PUT 凭据键经 extra=ignore 静默忽略（200 但不落盘、不新增凭据键）
- PUT 未知键被忽略（不在 _DEFAULT_CONFIG 内）
- config.json 损坏时回退默认值（容错分支）
- 存量迁移三分支+warning 断言（lifespan，TestLegacyCredentialMigration）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _config_file(db_path: Path) -> Path:
    return db_path.parent / "config.json"


def _write_config_file(db_path: Path, payload: dict) -> None:
    _config_file(db_path).write_text(json.dumps(payload), encoding="utf-8")


class TestConfigGet:
    def test_get_returns_defaults_when_no_file(self, api_client: TestClient) -> None:
        """无 config.json 时 GET 返回完整默认配置。"""
        response = api_client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "zh-CN"
        assert data["timezone"] == "UTC+8"
        assert data["init_cash"] == 100000
        assert data["maker_fee"] == 0.1
        assert data["taker_fee"] == 0.1
        assert data["max_positions"] == 1
        # S-01 拆链：默认响应零凭据键（凭据唯一通道=credentials.json）
        assert "binance_api_key" not in data
        assert "binance_secret_key" not in data
        assert "binance_connected" not in data

    def test_get_drops_credential_keys_from_response(
        self, api_client: TestClient, db_path: Path,
    ) -> None:
        """S-01 拆链：GET 响应零凭据键（含迁移前存量窗口防明文回显）。"""
        _write_config_file(db_path, {
            "binance_api_key": "abcdefghij",
            "binance_secret_key": "ab",
            "binance_connected": True,
        })
        response = api_client.get("/api/config")
        assert response.status_code == 200
        body = response.json()
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in body, f"拆链后 GET 响应不应含 {k}"

    def test_get_merges_saved_with_defaults(self, api_client: TestClient, db_path: Path) -> None:
        """存量文件只存部分键时，与默认值合并返回。"""
        _write_config_file(db_path, {"language": "en-US"})
        data = api_client.get("/api/config").json()
        assert data["language"] == "en-US"
        assert data["timezone"] == "UTC+8"  # 未存键回默认

    def test_get_falls_back_on_corrupt_file(self, api_client: TestClient, db_path: Path) -> None:
        """config.json 内容非法 JSON 时回退默认值（不 500）。"""
        _config_file(db_path).write_text("{not valid json", encoding="utf-8")
        response = api_client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["language"] == "zh-CN"


class TestConfigPut:
    def test_put_updates_known_key_and_persists(self, api_client: TestClient) -> None:
        """PUT 更新已知键：响应生效且落盘（再次 GET 仍生效）。"""
        response = api_client.put("/api/config", json={"language": "zh-TW", "init_cash": 50000})
        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "zh-TW"
        assert data["init_cash"] == 50000
        # 落盘验证：新 GET 反映同一值
        assert api_client.get("/api/config").json()["language"] == "zh-TW"

    def test_put_ignores_unknown_keys(self, api_client: TestClient) -> None:
        """不在 _DEFAULT_CONFIG 内的键被忽略，不写入。"""
        response = api_client.put("/api/config", json={"evil_key": "x", "timezone": "UTC+9"})
        assert response.status_code == 200
        assert "evil_key" not in response.json()
        assert response.json()["timezone"] == "UTC+9"

    def test_put_credential_keys_ignored_not_persisted(
        self, api_client: TestClient, db_path: Path,
    ) -> None:
        """S-01 拆链：ConfigUpdate 无凭据字段——客户端误传被 extra=ignore
        静默忽略（200 但不落盘、不新增凭据键）。"""
        _write_config_file(db_path, {"language": "zh-CN"})
        response = api_client.put("/api/config", json={
            "binance_api_key": "leaked-plain-value-99",
            "binance_secret_key": "another-plain",
            "binance_connected": True,
        })
        assert response.status_code == 200
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in response.json()
        saved = json.loads(_config_file(db_path).read_text(encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in saved, f"凭据键 {k} 不得经 PUT 落盘"

    def test_put_normal_keys_unchanged(self, api_client: TestClient) -> None:
        """S-01 拆链回归：普通配置键 PUT/GET 行为逐字节不变。"""
        response = api_client.put("/api/config", json={
            "language": "en-US", "init_cash": 200000})
        assert response.status_code == 200
        body = api_client.get("/api/config").json()
        assert body["language"] == "en-US"
        assert body["init_cash"] == 200000


class TestLegacyCredentialMigration:
    """S-01 存量迁移三分支（app lifespan 启动时执行；同源解析
    _config_path(db_path)）。"""

    def _run_migration(self, db_path: Path) -> None:
        from api.routes.config import migrate_legacy_credentials
        migrate_legacy_credentials(db_path)

    def test_branch1_migrates_and_deletes_keys(
        self, tmp_path, monkeypatch, caplog,
    ) -> None:
        """分支①：无凭据文件 → 自动迁移（写入+chmod 600）+物理删键
        +warning 说明迁移去向（tasks 3.2.3 验证锚）。"""
        cred = tmp_path / "creds" / "credentials.json"
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(cred))
        db_path = tmp_path / "db" / "quant.db"
        db_path.parent.mkdir(parents=True)
        (db_path.parent / "config.json").write_text(json.dumps({
            "binance_api_key": "k" * 64, "binance_secret_key": "s" * 64,
            "binance_connected": True, "language": "zh-CN",
        }), encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="api.routes.config"):
            self._run_migration(db_path)
        assert "已自动迁移" in caplog.text, "分支①应有迁移去向 warning"
        assert cred.exists(), "凭据文件应自动写入"
        # 注：不合法存量形态（值空/短→不炸启动+未迁移 warning+删键
        # 仍执行）由 test_branch1_invalid_legacy_value 承载（CARD-22）
        import stat as _stat
        import os as _os
        assert _stat.S_IMODE(_os.stat(cred).st_mode) == 0o600
        saved = json.loads(cred.read_text(encoding="utf-8"))
        assert saved["binance_api_key"] == "k" * 64
        cfg = json.loads((db_path.parent / "config.json").read_text(
            encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in cfg, f"分支①应物理删除 {k}"
        assert cfg["language"] == "zh-CN", "普通配置键逐字节保留"

    def test_branch1_invalid_legacy_value_warns_and_deletes(
        self, tmp_path, monkeypatch, caplog,
    ) -> None:
        """分支①不合法存量（CARD-22）：值空/长度异常 → 不炸启动、
        不迁移，warning 记录后仍物理删键（值不可用，删键不丢信息，
        防「残留再炸」死循环）。"""
        cred = tmp_path / "creds" / "credentials.json"
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(cred))
        db_path = tmp_path / "db" / "quant.db"
        db_path.parent.mkdir(parents=True)
        (db_path.parent / "config.json").write_text(json.dumps({
            "binance_api_key": "k" * 64, "binance_secret_key": "",
            "binance_connected": True, "language": "zh-CN",
        }), encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="api.routes.config"):
            self._run_migration(db_path)  # 不得抛 CredentialError
        assert "未迁移" in caplog.text, "不合法存量应有未迁移 warning"
        assert not cred.exists(), "不合法值不应写入凭据文件"
        cfg = json.loads((db_path.parent / "config.json").read_text(
            encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in cfg, f"不合法存量 {k} 删键仍执行（防残留再炸）"
        assert cfg["language"] == "zh-CN", "普通配置键逐字节保留"

    def test_branch2_keeps_existing_credentials_file(
        self, tmp_path, monkeypatch, caplog,
    ) -> None:
        """分支②：凭据文件已有值 → 不覆盖+仅删三键+冲突 warning
        （凭据文件优先）。"""
        cred = tmp_path / "creds" / "credentials.json"
        cred.parent.mkdir(parents=True)
        cred.write_text(json.dumps({
            "binance_api_key": "existing" + "k" * 56,
            "binance_secret_key": "existing" + "s" * 56,
        }), encoding="utf-8")
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(cred))
        db_path = tmp_path / "db" / "quant.db"
        db_path.parent.mkdir(parents=True)
        (db_path.parent / "config.json").write_text(json.dumps({
            "binance_api_key": "other" + "k" * 59,
            "binance_secret_key": "other" + "s" * 59,
            "binance_connected": True,
        }), encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="api.routes.config"):
            self._run_migration(db_path)
        assert "不覆盖" in caplog.text, "分支②应有凭据文件优先冲突 warning"
        saved = json.loads(cred.read_text(encoding="utf-8"))
        assert saved["binance_api_key"].startswith("existing"), \
            "分支②不覆盖已有凭据文件"
        cfg = json.loads((db_path.parent / "config.json").read_text(
            encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in cfg, f"分支②仍物理删除 {k}"

    def test_branch2_partial_credentials_file_not_overwritten(
        self, tmp_path, monkeypatch, caplog,
    ) -> None:
        """分支②半迁移态（CARD-23）：凭据文件任一键非空即「已有
        非空值」——不覆盖（修复前 and 判定落分支①被整体覆盖丢值）、
        冲突 warning、仍物理删键；对齐 spec「凭据文件已有非空值时
        SHALL 不覆盖」。"""
        cred = tmp_path / "creds" / "credentials.json"
        cred.parent.mkdir(parents=True)
        cred.write_text(json.dumps({
            "binance_api_key": "kept" + "k" * 60,
            "binance_secret_key": "",
        }), encoding="utf-8")
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(cred))
        db_path = tmp_path / "db" / "quant.db"
        db_path.parent.mkdir(parents=True)
        (db_path.parent / "config.json").write_text(json.dumps({
            "binance_api_key": "other" + "k" * 59,
            "binance_secret_key": "other" + "s" * 59,
        }), encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="api.routes.config"):
            self._run_migration(db_path)
        assert "不覆盖" in caplog.text, "半迁移态应走分支②冲突 warning"
        saved = json.loads(cred.read_text(encoding="utf-8"))
        assert saved["binance_api_key"].startswith("kept"), \
            "半迁移态已有非空值不得被覆盖（CARD-23）"
        assert saved["binance_secret_key"] == "", "空键原样保持"
        cfg = json.loads((db_path.parent / "config.json").read_text(
            encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key"):
            assert k not in cfg, f"半迁移态仍物理删除 {k}"

    def test_branch3_no_credentials_zero_action(
        self, tmp_path, monkeypatch, caplog,
    ) -> None:
        """分支③：config.json 无凭据键 → 零动作（文件原样、无日志）。"""
        cred = tmp_path / "creds" / "credentials.json"
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(cred))
        db_path = tmp_path / "db" / "quant.db"
        db_path.parent.mkdir(parents=True)
        original = {"language": "zh-CN", "init_cash": 100}
        (db_path.parent / "config.json").write_text(json.dumps(original),
                                                    encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="api.routes.config"):
            self._run_migration(db_path)
        assert not caplog.records, "分支③零动作不应产生日志"
        assert not cred.exists(), "分支③不应产生凭据文件"
        cfg = json.loads((db_path.parent / "config.json").read_text(
            encoding="utf-8"))
        assert cfg == original

    def test_migrated_config_file_has_no_credential_keys(
        self, tmp_path, monkeypatch,
    ) -> None:
        """迁移后链路面：config.json 文件零凭据键（拆链+迁移双保险；
        GET 响应面由 test_get_drops_credential_keys_from_response 与
        tests/infra TestLifespanMigrationE2E 承载——本用例名实对齐
        只测文件面，CARD-27-3 改名）。"""
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE",
                           str(tmp_path / "creds.json"))
        db_path = tmp_path / "db" / "quant.db"
        db_path.parent.mkdir(parents=True)
        (db_path.parent / "config.json").write_text(json.dumps({
            "binance_api_key": "k" * 64, "binance_secret_key": "s" * 64,
            "binance_connected": True,
        }), encoding="utf-8")
        self._run_migration(db_path)
        cfg = json.loads((db_path.parent / "config.json").read_text(
            encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in cfg, f"迁移后 config.json 不应残留 {k}"


class TestPutTypeValidation:
    """CARD-29：ConfigUpdate 类型锁定（旧 Dict[str, Any] 全放行）。"""

    def test_dict_value_for_int_key_422(self, api_client: TestClient) -> None:
        """init_cash 传 dict → 422（旧 200 落盘任意结构）。"""
        response = api_client.put(
            "/api/config", json={"init_cash": {"evil": [1, 2]}},
        )
        assert response.status_code == 422

    def test_bool_for_int_field_422(self, api_client: TestClient) -> None:
        """max_positions 传 bool → 422（lax 下 True→1 的隐式收窄被拒）。"""
        response = api_client.put("/api/config", json={"max_positions": True})
        assert response.status_code == 422

    def test_list_for_bool_field_422(self, api_client: TestClient) -> None:
        """bool 字段传 list → 422（注：lax 下 "yes" 等字符串属合法 bool
        解析集，与数字字符串→int 同为保留的 lax 行为，不在收严面）。"""
        response = api_client.put(
            "/api/config", json={"notify_signal": [True]},
        )
        assert response.status_code == 422

    def test_list_for_str_field_422(self, api_client: TestClient) -> None:
        response = api_client.put(
            "/api/config", json={"timezone": ["UTC+8"]},
        )
        assert response.status_code == 422

    def test_valid_typed_update_passes(self, api_client: TestClient) -> None:
        """类型合法的部分更新正常生效（int/float/bool 均过）。"""
        response = api_client.put("/api/config", json={
            "init_cash": 200000, "maker_fee": 0.05,
            "notify_signal": False, "language": "zh-CN",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["init_cash"] == 200000
        assert data["maker_fee"] == 0.05
        assert data["notify_signal"] is False

    def test_partial_update_leaves_unmentioned_keys(
        self, api_client: TestClient,
    ) -> None:
        """exclude_unset 部分更新：未传键保持原值。"""
        api_client.put("/api/config", json={"init_cash": 300000})
        response = api_client.put("/api/config", json={"language": "en-US"})
        data = response.json()
        assert data["language"] == "en-US"
        assert data["init_cash"] == 300000  # 未提及不动

    def test_explicit_null_written_as_null(self, api_client: TestClient, db_path: Path) -> None:
        """显式传 JSON null 落 null（与原 Dict 行为一致，未扩收严面）。"""
        response = api_client.put("/api/config", json={"notify_evolution": None})
        assert response.status_code == 200
        saved = json.loads(_config_file(db_path).read_text(encoding="utf-8"))
        assert saved["notify_evolution"] is None

    def test_unknown_key_ignored_still(self, api_client: TestClient) -> None:
        """未知键静默忽略（extra="ignore"，与旧白名单效果一致）。"""
        response = api_client.put(
            "/api/config", json={"evil_key": "x", "timezone": "UTC+9"},
        )
        assert response.status_code == 200
        assert "evil_key" not in response.json()
        assert response.json()["timezone"] == "UTC+9"
