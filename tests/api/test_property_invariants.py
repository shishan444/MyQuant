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
    def test_any_nonempty_value_masks_fully(self) -> None:
        """任意非空输入（含 unicode/空白/超长/含 *** 本身）恒 "***"。"""
        from api.routes.config import _mask
        random.seed(7)
        samples = [
            "a", "x" * 5000, "***", "a***z", "  ", "中文凭证",
            "".join(random.choice("abc XYZ09") for _ in range(64)),
        ]
        for value in samples:
            assert _mask(value) == "***", f"输入 {value!r} 未全掩码"

    def test_empty_always_empty(self) -> None:
        from api.routes.config import _mask
        assert _mask("") == ""

    def test_masked_view_only_touches_credential_keys(self) -> None:
        """_masked_view 只掩凭证键，其余键原样透传（任意配置 dict）。"""
        from api.routes.config import _CREDENTIAL_KEYS, _masked_view
        cfg = {
            "language": "zh-CN", "init_cash": 1,
            "binance_api_key": "secret", "maker_fee": 0.1,
        }
        view = _masked_view(cfg)
        assert view["binance_api_key"] == "***"
        assert view["language"] == "zh-CN"
        assert view["init_cash"] == 1
        # 非凭证键无掩码副作用
        masked_keys = {k for k, v in view.items() if v == "***"}
        assert masked_keys <= set(_CREDENTIAL_KEYS)


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
