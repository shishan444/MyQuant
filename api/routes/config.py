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


def migrate_legacy_credentials(db_path: Path) -> None:
    """存量凭据一次性迁移清理（fix-audit-disclosure-security S-01）。

    app lifespan 启动时执行；被检测 config.json 经 _config_path(db_path)
    同源解析（与 config API 读写同一文件——MYQUANT_DB_PATH 切库/e2e
    随机库场景即该运行库目录的 config.json）。三分支：
    ①credentials.json 不存在或两键均空 → 自动迁移（写入+chmod 600）
      +物理删除 config.json 凭据键（非置空——merge 回显风险在案）
      +warning 说明迁移去向；
    ②credentials.json 已有非空值 → 不覆盖（凭据文件优先）仅删
      config.json 凭据键+冲突 warning 提示待用户确认；
    ③config.json 无凭据键 → 零动作。
    多库语义：各库凭据均迁至同一固定凭据文件（credentials.json 不随
    db_path 走——环境身份唯一）。
    """
    import logging
    import os

    from core.live_trading.credentials import _credentials_path, save_credentials

    logger = logging.getLogger(__name__)
    cfg_path = _config_path(db_path)
    if not cfg_path.exists():
        return
    try:
        with open(cfg_path, encoding="utf-8") as f:
            saved = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return  # config.json 损坏：普通配置读取面既有语义承载，迁移零动作
    if not isinstance(saved, dict):
        return
    legacy_keys = ("binance_api_key", "binance_secret_key")
    legacy = {k: saved.get(k) for k in legacy_keys}
    has_legacy = any(legacy.get(k) for k in legacy_keys)
    if not has_legacy:
        return  # ③无凭据键（或全空）→ 零动作

    cred_path = _credentials_path()
    existing: Dict[str, Any] = {}
    if cred_path.exists():
        try:
            existing = json.loads(cred_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            existing = {}
    cred_has_value = bool(existing.get("binance_api_key")) and         bool(existing.get("binance_secret_key")) if isinstance(existing, dict) else False

    if cred_has_value:
        # ②凭据文件已有非空值：不覆盖（凭据文件优先）仅删键+冲突提示
        logger.warning(
            "config.json 检出存量币安凭据，但凭据文件 %s 已有配置——"
            "不覆盖（凭据文件优先），仅移除 config.json 凭据键；"
            "若 config.json 中的值才是想要的，请手工确认后覆写凭据文件。",
            cred_path,
        )
    else:
        # ①自动迁移：写入凭据文件（两键形制+chmod 600）；值不合法
        # （空/长度异常）按无效存量处理——warning 记录后仍物理删键，
        # 保住迁移链不炸启动（值本身不可用，删键不丢信息）
        from core.live_trading.credentials import BinanceCredentials, CredentialError
        try:
            save_credentials(BinanceCredentials(
                api_key=str(legacy["binance_api_key"]),
                api_secret=str(legacy["binance_secret_key"]),
            ))
            logger.warning(
                "config.json 检出存量币安凭据——已自动迁移至凭据文件 %s"
                "（chmod 600），config.json 凭据键已移除。凭据唯一通道为"
                "该文件（MYQUANT_CREDENTIALS_FILE 可覆写路径）。",
                cred_path,
            )
        except CredentialError:
            logger.warning(
                "config.json 检出存量币安凭据但值不合法（空/长度异常）——"
                "未迁移，请人工经凭据文件 %s 配置；config.json 存量凭据键"
                "将移除（值本身不可用，删键不丢信息）。",
                cred_path,
            )

    # 物理删除 config.json 凭据键（含 binance_connected；非置空——
    # _read_config merge 形制下置空保留键使 GET 语义含糊）
    changed = False
    for k in _RETIRED_CRED_KEYS:
        if k in saved:
            del saved[k]
            changed = True
    if changed:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2, ensure_ascii=False)
        os.chmod(cfg_path, 0o600)
