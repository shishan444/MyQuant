"""U1-3 披露一致性（add-math-formula-vocabulary）。

读 mcpserver/tools.py 与 mcpserver/capability.py 源文本，断言词数
披露 26 一致、新五词按归族口径正确出现（非逐字列全词名——docstring
沿族代表词形制）、文件内无旧词数披露字样残留。spec.md 文本断言不进
pytest（工程外路径），转 L3 交付检查。
"""

import re
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

    #: 新五词归族口径（design §4）：词名 → 应归入的族句
    _FAMILY_WORDS = {
        "时序变换": ("rolling_quantile",),
        "逐元素": ("sqrt", "log"),
        "卡尔曼封装": ("kalman", "kalman_slope"),
    }

    @staticmethod
    def _family_clause(text: str, start_pattern: str, seps: str) -> str:
        """提取族句段：start_pattern 起至 seps 中任一分隔符止
        （跨行文本，[^sep] 含换行）。capability 侧族句前另有非族句
        出现（如「时序结构与逐元素逻辑」），start_pattern 须带族句
        特征（计数形制）防误锚。"""
        m = re.search(start_pattern, text)
        assert m, f"族句段锚未命中: {start_pattern}"
        tail = text[m.start():]
        end = re.search(f"[{re.escape(seps)}]", tail)
        return tail[:end.start()] if end else tail

    def test_tools_docstring_word_count_26(self):
        text = _tools_text()
        assert "白名单函数 26 词" in text

    def test_capability_word_count_26(self):
        text = _capability_text()
        assert "白名单 26 词" in text

    def test_capability_family_counts_sum_to_26(self):
        """capability 族计数句内自洽（提取式对账——计数从文本提取，
        非字面量恒等式）：三显式族计数+时序结构词列举实数，加总==
        披露总词数（design §4 归族对账 12+2+10+2=26 口径）。"""
        text = _capability_text()
        m = re.search(r"白名单 (\d+) 词", text)
        assert m, "总词数披露缺席"
        disclosed = int(m.group(1))
        total = 0
        for family in ("时序变换", "逐元素", "卡尔曼封装"):
            fm = re.search(rf"{family} (\d+)", text)
            assert fm, f"{family} 族计数缺席"
            total += int(fm.group(1))
        sm = re.search(r"时序结构\s+(\w+(?:/\w+)+)", text)
        assert sm, "时序结构词列举缺席"
        total += len(sm.group(1).split("/"))
        assert total == disclosed, \
            f"族计数加总 {total} != 披露总词数 {disclosed}"

    def test_new_five_words_classified_in_disclosure(self):
        """新五词按归族口径在两披露面正确归类出现：词名与族名同句段
        共现（句段=族句起至下一分隔符——tools 以「；」切、capability
        以「+」切）；词边界匹配防 logical_* 对 log 的子串误命中与
        kalman_slope 对 kalman 的前缀误命中。杀变口径：单删任一族
        的任一新词披露（或挪出族句）本用例必红。"""
        for text, start_fmt, seps in (
                (_tools_text(), "{family}", "；"),
                (_capability_text(), "{family} \\d+", "+")):
            for family, words in self._FAMILY_WORDS.items():
                clause = self._family_clause(
                    text, start_fmt.format(family=family), seps)
                for w in words:
                    assert re.search(rf"\b{w}\b", clause), \
                        f"{w} 未归入 {family} 族句段"

    def test_no_old_word_count_residue(self):
        """扩容后披露面零旧词数披露字样残留（design §4 同步纪律；
        断言串拼接书写防本文件自身成为 grep 命中源）。"""
        old_phrase = "21" + " 词"
        for text in (_tools_text(), _capability_text()):
            assert old_phrase not in text
