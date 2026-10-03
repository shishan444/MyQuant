"""属性不变量特征网（阶段四属性补强，V3 锚点分类：属性/不变量）。

对纯计算函数验证一类输入应保持的关系（明确成立条件）：
- compute_verify_star：全 qualified 前提下 star 随 avg_fitness 单调不减，
  且阈值边界处正确跳变（人工核算锚在 test_verify_star.py，此处补
  单调性属性）
- _mask/_masked_view：任意非空凭证恒全掩码、空恒空串（不依赖输入形态）
- _safe_error_message：任意异常实例消息恒取自有限值域
  {数据文件缺失*, 内部错误}（不泄露 str(exc) 原文）
"""
from __future__ import annotations

import random

import pytest

pytestmark = [pytest.mark.unit]


class TestComputeVerifyStarMonotonicity:
    def _star(self, fitness: float) -> int:
        from api.routes.strategies.verify import compute_verify_star
        return compute_verify_star(fitness, qualified_count=1, total_periods=1)

    def test_star_monotonic_nondecreasing_over_fitness(self) -> None:
        """全 qualified 前提：fitness 递增序列的 star 单调不减。"""
        random.seed(42)
        fitnesses = sorted(
            [random.uniform(-2.0, 10.0) for _ in range(200)]
            + [0.0, 1e-9, 2.0, 3.0, 4.5, 6.0]  # 阈值精确点并入后整体排序
        )
        stars = [self._star(f) for f in fitnesses]
        for i, (prev, cur) in enumerate(zip(stars, stars[1:])):
            assert cur >= prev, (
                f"位置 {i}（fitness {fitnesses[i]:.4f}->{fitnesses[i+1]:.4f}）"
                f"star {prev}->{cur} 下降"
            )

    def test_star_threshold_jumps(self) -> None:
        """阈值边界跳变：恰阈值取高档、阈值下取低档。"""
        assert self._star(5.999999) == 4
        assert self._star(6.0) == 5
        assert self._star(4.499999) == 3
        assert self._star(4.5) == 4

    def test_not_all_qualified_always_zero(self) -> None:
        """非全 qualified 恒 0 星（与 fitness 无关）。"""
        from api.routes.strategies.verify import compute_verify_star
        for fitness in (-1.0, 0.5, 3.0, 9.9):
            assert compute_verify_star(fitness, 0, 1) == 0
            assert compute_verify_star(fitness, 2, 5) == 0


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
