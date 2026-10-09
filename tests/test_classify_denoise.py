"""分类优先级 + 去噪边界测试."""
import re

from tests._runner import assert_eq, assert_true

from doc2md import config as cfgmod
from doc2md import denoise

CASES = []


def test_mapping_priority_over_keywords():
    """mapping 命中时不用 keywords (mapping 优先级最高)."""
    c = cfgmod.Config({
        "taxonomy": ["cat-a", "cat-b"],
        "default_category": "def",
        "mapping": {"report.txt": "cat-a"},
        "keywords": {"cat-b": ["report"]},  # report 同时在 keywords, 但 mapping 应优先
    })
    assert_eq(c.classify("report.txt", "report content"), "cat-a",
              "mapping 应优先于 keywords")


CASES.append(("mapping_priority_over_keywords", test_mapping_priority_over_keywords))


def test_keywords_count():
    """keywords 命中数最多者胜出."""
    c = cfgmod.Config({
        "taxonomy": ["cat-a", "cat-b"],
        "default_category": "def",
        "keywords": {
            "cat-a": ["alpha"],
            "cat-b": ["beta", "gamma", "delta"],
        },
    })
    # cat-b 命中 3 个, cat-a 命中 1 个
    result = c.classify("file.txt", "alpha beta gamma delta")
    assert_eq(result, "cat-b", "命中数最多者应胜出")


CASES.append(("keywords_count", test_keywords_count))


def test_no_keyword_match_defaults():
    """无任何关键词命中 -> default_category."""
    c = cfgmod.Config({
        "taxonomy": ["cat-a"],
        "default_category": "unclassified",
        "keywords": {"cat-a": ["specific"]},
    })
    assert_eq(c.classify("file.txt", "no matching words here"), "unclassified")


CASES.append(("no_keyword_match_defaults", test_no_keyword_match_defaults))


def test_clean_text_collapses_blanks():
    """多空行折叠为 1, 行尾空白去除, 首尾空行去除."""
    raw = "  \n\n\nline1  \n\n\n\nline2\n\n"
    result = denoise.clean_text(raw)
    assert_true(not result.startswith("\n"), "首部无空行")
    assert_true(not result.endswith("\n"), "尾部无空行")
    lines = result.split("\n")
    blanks = [l for l in lines if l == ""]
    assert_true(len(blanks) <= 1, "连续空行应折叠为 1")
    assert_true("line1" in result and "line2" in result)


CASES.append(("clean_text_collapses_blanks", test_clean_text_collapses_blanks))


def test_strip_boilerplate_page_numbers():
    """页码行被去除, 普通数字行保留."""
    text = "正文第一段\n第 3 页\npage 5\n- 7 -\n42\n正文第二段\n"
    result = denoise.strip_boilerplate(text)
    assert_true("第 3 页" not in result, "中文页码应去除")
    assert_true("page 5" not in result, "英文页码应去除")
    assert_true("- 7 -" not in result, "横线页码应去除")
    assert_true("42" in result, "纯数字可能是正文, 不应去除")
    assert_true("正文第一段" in result and "正文第二段" in result)


CASES.append(("strip_boilerplate_page_numbers", test_strip_boilerplate_page_numbers))


def test_summary_all_headings_empty():
    """全标题无正文 -> summary 为空."""
    text = "# Title\n## Sub1\n## Sub2\n### Sub3\n"
    result = denoise.extract_summary(text)
    assert_eq(result, "", "全标题应无 summary")


CASES.append(("summary_all_headings_empty", test_summary_all_headings_empty))


def test_summary_all_code_empty():
    """全代码无正文 -> summary 为空."""
    text = "```\ncode line 1\ncode line 2\n```\n"
    result = denoise.extract_summary(text)
    assert_eq(result, "", "全代码应无 summary")


CASES.append(("summary_all_code_empty", test_summary_all_code_empty))
