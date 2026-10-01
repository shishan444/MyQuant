"""templates 特征测试网（现状记录语义）。

模块标记 DEPRECATED；锚定模板清单的完整序与别名解析的共享实例
语义，防止别名增删或解析改为拷贝时静默漂移。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.templates import (
    SCORING_TEMPLATES,
    _TEMPLATE_ALIASES,
    get_template,
    list_template_names,
)


class TestTemplateInventory:
    def test_full_name_inventory_in_order(self):
        # 3 个核心模板 + 7 个别名，按 dict 定义序拼接（现状清单）
        assert list_template_names() == [
            "explorer", "optimizer", "max_return",
            "balanced", "steady", "custom",
            "aggressive", "profit_first", "conservative", "risk_first",
        ]


class TestAliasResolution:
    @pytest.mark.parametrize(
        ("alias", "core"),
        [
            pytest.param("balanced", "optimizer", id="balanced"),
            pytest.param("steady", "optimizer", id="steady"),
            pytest.param("custom", "optimizer", id="custom"),
            pytest.param("aggressive", "explorer", id="aggressive"),
            pytest.param("profit_first", "explorer", id="profit_first"),
            pytest.param("conservative", "optimizer", id="conservative"),
            pytest.param("risk_first", "optimizer", id="risk_first"),
        ],
    )
    def test_alias_resolves_to_shared_instance(self, alias, core):
        # 别名解析返回核心模板的同一实例（不拷贝）
        assert _TEMPLATE_ALIASES[alias] == core
        assert get_template(alias) is SCORING_TEMPLATES[core]
