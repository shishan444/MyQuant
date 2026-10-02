"""Config CRUD routes - GET/PUT /api/config."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_db_path

router = APIRouter(prefix="/api/config", tags=["config"])

# B9.2 S1：凭证键集合（GET masked 回显 + PUT 哨兵识别——前端零改动）
_CREDENTIAL_KEYS = ("binance_api_key", "binance_secret_key")
_MASK = "***"


def _mask(value: str) -> str:
    """全掩码回显（不泄露任何片段）；未配置回 ""。"""
    if not value:
        return ""
    return _MASK

_DEFAULT_CONFIG: Dict[str, Any] = {
    "language": "zh-CN",
    "timezone": "UTC+8",
    "notify_evolution": True,
    "notify_signal": True,
    "binance_api_key": "",
    "binance_secret_key": "",
    "binance_connected": False,
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


def _masked_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """凭证键 masked 后的完整回显视图（GET 与 PUT 响应共用）。"""
    return {
        **cfg,
        **{k: _mask(str(cfg[k])) for k in _CREDENTIAL_KEYS if k in cfg},
    }


@router.get("")
def get_config(db_path: Path = Depends(get_db_path)) -> Dict[str, Any]:
    cfg = _read_config(db_path)
    # S1：凭证键 masked 回显（显式声明不做文件加密——本地单用户威胁模型）
    return _masked_view(cfg)


@router.put("")
def update_config(
    payload: Dict[str, Any],
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    current = _read_config(db_path)
    for key, value in payload.items():
        if key in _DEFAULT_CONFIG:
            # S1 哨兵语义：含 *** 的凭证值=未修改回显，不覆盖已存值
            if key in _CREDENTIAL_KEYS and isinstance(value, str) and _MASK in value:
                continue
            current[key] = value
    _write_config(db_path, current)
    # 响应同样 masked（与 GET 一致）
    return _masked_view(current)
