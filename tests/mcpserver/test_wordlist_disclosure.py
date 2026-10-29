"""U1-3 披露一致性（add-math-formula-vocabulary）。

读 mcpserver/tools.py 与 mcpserver/capability.py 源文本，断言词数
披露 26 一致、新五词按归族口径正确出现（非逐字列全词名——docstring
沿族代表词形制）、文件内无旧词数披露字样残留。spec.md 文本断言不进
pytest（工程外路径），转 L3 交付检查。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_TOOLS = Path(__file__).resolve().parents[2] / "mcpserver" / "tools.py"
_CAPABILITY = Path(__file__).resolve().parents[2] / "mcpserver" / "capability.py"


def _tools_text() -> str:
    return _TOOLS.read_text(encoding="utf-8")


def _capability_text() -> str:
    return _CAPABILITY.read_text(encoding="utf-8")


class TestWordlistDisclosure:
    """26 词披露三面：词数一致+归族出现+零残留。"""

    def test_tools_docstring_word_count_26(self):
        text = _tools_text()
        assert "白名单函数 26 词" in text

    def test_capability_word_count_26(self):
        text = _capability_text()
        assert "白名单 26 词" in text

    def test_capability_family_counts_sum_to_26(self):
        """capability 族计数句内自洽：时序变换 12 + 时序结构 2 +
        逐元素 10 + 卡尔曼封装 2 = 26（design §4 归族对账口径）。"""
        text = _capability_text()
        assert "时序变换 12" in text
        assert "逐元素 10" in text
        assert "卡尔曼封装 2" in text
        assert 12 + 2 + 10 + 2 == 26

    def test_new_five_words_classified_in_disclosure(self):
        """新五词按归族口径在两披露面正确归类出现：
        rolling_quantile→时序变换、sqrt/log→逐元素、
        kalman/kalman_slope→卡尔曼封装。"""
        tools = _tools_text()
        cap = _capability_text()
        for text in (tools, cap):
            for w in ("rolling_quantile", "sqrt", "log",
                      "kalman_slope", "kalman"):
                assert w in text, (w, "五词须在词族描述中出现")

    def test_no_old_word_count_residue(self):
        """扩容后披露面零旧词数披露字样残留（design §4 同步纪律；
        断言串拼接书写防本文件自身成为 grep 命中源）。"""
        old_phrase = "21" + " 词"
        for text in (_tools_text(), _capability_text()):
            assert old_phrase not in text
