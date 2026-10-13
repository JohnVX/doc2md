"""回归测试: 针对实际产物检查中发现的 4 个缺陷的修复.

1. 表格单元格 \\x0b (垂直制表符) 换行 -> 表格断裂
2. PDF 元数据占位标题 (如 "幻灯片 1") 被采信
3. 装饰图/logo OCR 出 1-2 字符噪音 -> 污染正文
4. 纯表格文档 summary 为空
"""
from tests._runner import assert_eq, assert_true

from doc2md import ocr, util
from doc2md.denoise import clean_text
from doc2md.parsers._common import md_table
from doc2md.pipeline import _table_summary_fallback

CASES = []


# ---------- BUG 1: 表格 \\x0b 换行 / 双重转义 ----------

def test_md_table_vtab_not_break_row():
    """单元格含 \\x0b (PPT 单元格内换行) 应转为空格, 不拆断表格行."""
    rows = [["表头A", "表头B"], ["第一段\x0b第二段", "值"]]
    out = md_table(rows, 2)
    lines = out.split("\n")
    assert_eq(len(lines), 3, "应为 表头+分隔+1数据 行, \\x0b 不能拆行")
    assert_true("\x0b" not in out, "输出不应残留 \\x0b")
    assert_true("第一段 第二段" in out, "\\x0b 应转为空格")
    assert_true(lines[2].startswith("|") and lines[2].endswith("|"),
                "数据行应完整以 | 开头结尾")


def test_md_table_no_double_escape_pipe():
    """单元格内 | 只应转义一次 (\\|), 不能变 \\\\\\|."""
    rows = [["h"], ["a|b"]]
    out = md_table(rows, 1)
    assert_true("a\\|b" in out, "单次转义")
    assert_true("a\\\\\\|b" not in out, "不应双重转义")


def test_clean_text_keeps_vtab():
    """clean_text 不应把 \\x0b 当换行 (splitlines 的坑)."""
    result = clean_text("第一段\x0b第二段")
    assert_eq(result, "第一段\x0b第二段", "\\x0b 应原样保留 (交后续转义处理)")
    assert_true("\n" not in result, "不应引入换行")


CASES.append(("md_table_vtab_not_break_row", test_md_table_vtab_not_break_row))
CASES.append(("md_table_no_double_escape_pipe", test_md_table_no_double_escape_pipe))
CASES.append(("clean_text_keeps_vtab", test_clean_text_keeps_vtab))


# ---------- BUG 2: PDF 占位标题 ----------

def test_pick_title_rejects_placeholder_not_in_body():
    """"幻灯片 1" 不在正文中 -> 不可信 -> 回退文件名."""
    body = "《常见攻击模式2.0》\n可信系统设计认证赋能\n讲师介绍\n"
    title = util.pick_title(["幻灯片 1"], "07_常见攻击模式", body=body)
    assert_eq(title, "07_常见攻击模式", "占位标题不在正文, 应回退文件名")


def test_pick_title_accepts_meta_title_in_body():
    """真实元数据标题出现在正文中 -> 采信."""
    body = "华为C语言编程规范V5.2\n正文内容...\n"
    title = util.pick_title(["华为C语言编程规范V5.2"], "somefile", body=body)
    assert_eq(title, "华为C语言编程规范V5.2", "标题在正文中, 应采信")


def test_pick_title_body_none_keeps_old_behavior():
    """不传 body 时保持旧行为 (向后兼容 docx/pptx 调用)."""
    title = util.pick_title(["幻灯片 1"], "stem")
    assert_eq(title, "幻灯片 1", "无 body 时不额外过滤")


def test_pick_title_short_candidate_not_rejected():
    """过短候选 (1 字符) 无法判断, 不因内容一致性被否 (min_len 保护)."""
    body = "完全无关的正文"
    title = util.pick_title(["X"], "stem", body=body)
    assert_eq(title, "X", "1 字符标题 (<min_len) 不做内容一致性判定")


CASES.append(("pick_title_rejects_placeholder_not_in_body",
              test_pick_title_rejects_placeholder_not_in_body))
CASES.append(("pick_title_accepts_meta_title_in_body",
              test_pick_title_accepts_meta_title_in_body))
CASES.append(("pick_title_body_none_keeps_old_behavior",
              test_pick_title_body_none_keeps_old_behavior))
CASES.append(("pick_title_short_candidate_not_rejected",
              test_pick_title_short_candidate_not_rejected))


# ---------- BUG 3: OCR 噪音 ----------

def test_ocr_noise_short_is_noise():
    """极短结果 (1-2 字符) 判为噪音, 即便置信度高."""
    assert_true(ocr._is_ocr_noise(["G"], [0.73]), "单字符应判噪音")
    assert_true(ocr._is_ocr_noise(["C"], [0.90]), "单字符高置信仍是噪音")
    assert_true(ocr._is_ocr_noise(["AB"], [0.5]), "2 字符也算噪音")


def test_ocr_noise_real_text_kept():
    """真实短文本 (>=3 字符) 不应判为噪音."""
    assert_true(not ocr._is_ocr_noise(["HUAWEI"], [0.94]), "6 字符是真文字")
    assert_true(not ocr._is_ocr_noise(["图11"], [0.6]), "3 字符保留")
    assert_true(not ocr._is_ocr_noise(["abc"], [0.5]), "3 字符保留")


def test_ocr_noise_empty_not_noise():
    """空结果不算 '噪音' (由上层按无文字处理)."""
    assert_true(not ocr._is_ocr_noise([], []), "空列表不判噪音")


CASES.append(("ocr_noise_short_is_noise", test_ocr_noise_short_is_noise))
CASES.append(("ocr_noise_real_text_kept", test_ocr_noise_real_text_kept))
CASES.append(("ocr_noise_empty_not_noise", test_ocr_noise_empty_not_noise))


# ---------- BUG 4: 纯表格 summary ----------

def test_table_summary_fallback_for_pure_table():
    """纯表格正文 -> 返回表格规模占位摘要."""
    body = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    s = _table_summary_fallback(body)
    assert_true(s, "纯表格应给出占位摘要")
    assert_true("表格" in s, "摘要应说明是表格文档")


def test_table_summary_fallback_none_for_plain_text():
    """无表格的正文 -> 无占位摘要 (返回空)."""
    assert_eq(_table_summary_fallback("普通段落文字, 没有表格"), "", "无表格不生成")


CASES.append(("table_summary_fallback_for_pure_table",
              test_table_summary_fallback_for_pure_table))
CASES.append(("table_summary_fallback_none_for_plain_text",
              test_table_summary_fallback_none_for_plain_text))
