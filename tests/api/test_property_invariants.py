"""属性不变量特征网（阶段四属性补强，V3 锚点分类：属性/不变量）。

对纯计算函数验证一类输入应保持的关系（明确成立条件）：
- compute_verify_star 属性段已随旧验证会话体系退役
  （retire-verify-sessions）
- _mask/_masked_view：任意非空凭证恒全掩码、空恒空串（不依赖输入形态）
- _safe_error_message：任意异常实例消息恒取自有限值域
  {数据文件缺失*, 内部错误}（不泄露 str(exc) 原文）
"""
from __future__ import annotations

import random

import pytest

pytestmark = [pytest.mark.unit]


class TestMaskInvariant:
    """S-01 拆链后形制：掩码函数族已随凭据通道退役——原不变量面
    （_mask/_masked_view）改承载为「响应零凭据键」不变量
    （fix-audit-disclosure-security）。"""

    def test_response_view_drops_all_credential_keys(self) -> None:
        """任意配置 dict（含存量凭据键）→ 响应视图零凭据键、其余
        键原样透传（迁移窗口防明文回显的不变量承载）。"""
        from api.routes.config import _without_credentials
        cfg = {
            "language": "zh-CN", "init_cash": 1,
            "binance_api_key": "secret", "binance_secret_key": "s2",
            "binance_connected": True, "maker_fee": 0.1,
        }
        view = _without_credentials(cfg)
        assert "binance_api_key" not in view
        assert "binance_secret_key" not in view
        assert "binance_connected" not in view
        assert view["language"] == "zh-CN"
        assert view["init_cash"] == 1
        assert view["maker_fee"] == 0.1


class TestSafeErrorMessageRange:
    def test_any_exception_message_in_finite_domain(self) -> None:
        """任意异常实例 → 消息恒属 {数据文件缺失*, 内部错误}。

        不变量核心：str(exc) 原文永不进入返回值（防未来新增分支漏脱敏）。
        """
        from api.routes.strategies._common import _safe_error_message
        random.seed(11)
        exceptions = [
            RuntimeError("secret path /etc/x"), KeyError("k"),
            ValueError("v" * 300), TypeError(("t", 1)),
            FileNotFoundError(2, "No such file", "/a/b/c.parquet"),
            FileNotFoundError(2, "No such file and dir"),
            Exception(random.choice(["a", "内部细节", "***"])),
        ]
        for exc in exceptions:
            msg = _safe_error_message(exc)
            assert msg == "内部错误" or msg.startswith("数据文件缺失"), (
                f"{type(exc).__name__} 产出了值域外消息: {msg!r}"
            )
            # FNF 分支只允许 basename 级路径信息
            if msg.startswith("数据文件缺失: "):
                assert "/" not in msg, f"FNF 消息泄露路径: {msg!r}"

    def test_fnf_basename_only_never_dir(self) -> None:
        from api.routes.strategies._common import _safe_error_message
        exc = FileNotFoundError(2, "No such file", "/very/secret/dir/file.parquet")
        msg = _safe_error_message(exc)
        assert msg == "数据文件缺失: file.parquet"
        assert "secret" not in msg and "very" not in msg
