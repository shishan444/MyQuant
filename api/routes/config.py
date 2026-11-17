"""Config CRUD routes - GET/PUT /api/config."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_db_path
from api.schemas import ConfigUpdate

router = APIRouter(prefix="/api/config", tags=["config"])

# 退役凭据键集（fix-audit-disclosure-security S-01 拆链）：config 通道
# 不再承载凭据——GET/PUT 响应剔除存量键（迁移窗口防明文回显），PUT
# 形制已摘（ConfigUpdate 无凭据字段）。凭据唯一通道=data/credentials.json
_RETIRED_CRED_KEYS = ("binance_api_key", "binance_secret_key",
                      "binance_connected")


def _without_credentials(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """剔除凭据键的响应视图（拆链后 GET/PUT 共用；存量迁移前窗口
    防明文回显——迁移清理后 config.json 物理无这三键）。"""
    return {k: v for k, v in cfg.items() if k not in _RETIRED_CRED_KEYS}

_DEFAULT_CONFIG: Dict[str, Any] = {
    "language": "zh-CN",
    "timezone": "UTC+8",
    "notify_evolution": True,
    "notify_signal": True,
    "init_cash": 100000,
    "maker_fee": 0.1,
    "taker_fee": 0.1,
    "max_positions": 1,
}


def _config_path(db_path: Path) -> Path:
    return db_path.parent / "config.json"


def _read_config(db_path: Path) -> Dict[str, Any]:
    path = _config_path(db_path)
    if path.exists():
        try:
            with open(path) as f:
                saved = json.load(f)
            return {**_DEFAULT_CONFIG, **saved}
        except (json.JSONDecodeError, OSError):
            pass
    return {**_DEFAULT_CONFIG}


def _write_config(db_path: Path, config: Dict[str, Any]) -> None:
    path = _config_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    # SEC-01 轻量改善（team3 审查顺做，用户批准）：密钥可能落盘——
    # 属主读写权限收敛（本地单用户威胁模型下的文件权限面；加密存储
    # 升级归 live-trading 规划域）
    import os as _os
    _os.chmod(path, 0o600)


@router.get("")
def get_config(db_path: Path = Depends(get_db_path)) -> Dict[str, Any]:
    cfg = _read_config(db_path)
    # S-01 拆链：响应剔除凭据键（含迁移前存量窗口——零明文回显）
    return _without_credentials(cfg)


@router.put("")
def update_config(
    payload: ConfigUpdate,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    current = _read_config(db_path)
    # 部分更新：仅显式传入的字段（exclude_unset 区分未传与显式传参）。
    # S-01 拆链：ConfigUpdate 已无凭据字段（extra=ignore——客户端误传
    # 凭据键被静默忽略，不落盘）
    for key, value in payload.model_dump(exclude_unset=True).items():
        current[key] = value
    _write_config(db_path, current)
    return _without_credentials(current)


