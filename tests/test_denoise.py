"""denoise 测试: toc / summary 的围栏代码块感知."""
from tests._runner import assert_eq, assert_true

from doc2md import denoise

CASES = []


def test_toc_ignores_code_fence():
    body = "```cmake\n# import cmakeng support\ncmake_minimum_required(VERSION 3.16)\n# import cmakeng\n```\n"
    assert_eq(denoise.extract_toc(body), [], "代码块内的 # 注释不应进 toc")


CASES.append(("toc_ignores_code_fence", test_toc_ignores_code_fence))


def test_toc_keeps_real_headings():
    body = ("# 标题\n\n正文\n\n## 章节 A\n\n```python\n# code comment\n```\n\n### 子节\n")
    toc = denoise.extract_toc(body)
    assert_eq(toc, ["标题", "章节 A", "子节"])
    assert_true("code comment" not in toc)


CASES.append(("toc_keeps_real_headings", test_toc_keeps_real_headings))


def test_summary_skips_code_lines():
    # 纯代码文件: body 全是围栏, 摘要应为空(而非取代码行)
    body = "```cmake\nset_property(GLOBAL APPEND PROPERTY X)\nproject(y)\n```\n"
    assert_eq(denoise.extract_summary(body), "", "纯代码文件摘要应为空")


CASES.append(("summary_skips_code_lines", test_summary_skips_code_lines))


def test_summary_skips_code_with_internal_blank():
    # 代码块内部有空行(CMakeLists 真实形态): 内部段不被当摘要
    body = ("```cmake\n# import x\ncmake_minimum_required(VERSION 3.16)\nproject(y)\n\n"
            "# import z\ninclude(foo)\n```\n")
    assert_eq(denoise.extract_summary(body), "", "代码块内空行拆出的段不应进摘要")


CASES.append(("summary_skips_code_with_internal_blank", test_summary_skips_code_with_internal_blank))


def test_summary_from_prose():
    body = "# T\n\n- a\n- b\n\n这是一段正文摘要。继续。\n"
    assert_true(denoise.extract_summary(body).startswith("这是一段正文摘要"))


CASES.append(("summary_from_prose", test_summary_from_prose))
