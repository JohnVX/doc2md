"""pdf 解析测试: 大纲注入章节标题 + 扫描页(无文字层)OCR."""
import os
import tempfile

from tests import _samples
from tests._runner import (HAS_RAPIDOCR, assert_contains_any, assert_eq,
                            assert_true, cleanup, find_md_by_source,
                            find_mds, make_tmp_input, make_tmp_output,
                            read_md)

from doc2md import pipeline

CASES = []


def _gen_pdf_with_outline(path):
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((50, 72), "正文内容")
    doc.set_toc([[1, "第一章 概述", 1], [2, "1.1 背景", 1]])  # [级别,标题,页码]
    doc.save(path)
    doc.close()


def test_outline_injected_as_headings():
    in_dir = make_tmp_input()
    _gen_pdf_with_outline(os.path.join(in_dir, "toc.pdf"))
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    assert_eq(len(find_mds(out)), 1, "只应有 toc.pdf 一个 md")
    txt = read_md(out)
    assert_true("# 第一章 概述" in txt, "大纲应注入为一级标题")
    assert_true("## 1.1 背景" in txt, "大纲子节应注入为二级标题")
    cleanup(in_dir, out)


CASES.append(("outline_injected_as_headings", test_outline_injected_as_headings))


def test_scan_page_ocr():
    """无文字层的图片页 -> 渲染+OCR, 文本应进 md(无 rapidocr 则 stage2 标记)."""
    # png 放输入目录外, 避免被当独立图处理产生第二个 md
    png_dir = tempfile.mkdtemp()
    png = os.path.join(png_dir, "scan.png")
    _samples.gen_text_png(png, "SCANOCR77")
    try:
        in_dir = make_tmp_input()
        p = os.path.join(in_dir, "scanned.pdf")
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(page.rect, stream=open(png, "rb").read())  # 仅图, 无文字层
        doc.save(p)
        doc.close()
        out = make_tmp_output()
        _, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        assert_eq(len(find_mds(out)), 1, "只应有 scanned.pdf 一个 md")
        txt = find_md_by_source(out, "original-doc/scanned.pdf")
        assert_true("扫描页" in txt, "扫描页应进 md")
        if HAS_RAPIDOCR:
            assert_contains_any(txt, ["scan", "ocr", "77"], "扫描页应 OCR 出文字")
        else:
            assert_true("stage2" in txt, "无 OCR 引擎应留 stage2 标记")
        cleanup(in_dir, out)
    finally:
        cleanup(png_dir)


CASES.append(("scan_page_ocr", test_scan_page_ocr))


def test_inline_image_marker_on_text_page():
    """文字页含内嵌图(非整页扫描) -> 留 stage2 标记(图内文字未提取, 不强行 OCR)."""
    png_dir = tempfile.mkdtemp()
    png = os.path.join(png_dir, "logo.png")
    _samples.gen_text_png(png, "LOGOOCR11")
    try:
        in_dir = make_tmp_input()
        p = os.path.join(in_dir, "mixed.pdf")
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 72), "BodyText123")  # 有文字层(用拉丁文, fitz 内置字体可靠渲染)
        page.insert_image(fitz.Rect(50, 100, 200, 200), stream=open(png, "rb").read())  # 内嵌图
        doc.save(p)
        doc.close()
        out = make_tmp_output()
        _, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        txt = find_md_by_source(out, "original-doc/mixed.pdf")
        assert_true("bodytext" in txt.lower(), "文字层应提取")
        assert_true("inline-image" in txt, "文字页含内嵌图应留 stage2:inline-image 标记")
        cleanup(in_dir, out)
    finally:
        cleanup(png_dir)


CASES.append(("inline_image_marker_on_text_page", test_inline_image_marker_on_text_page))


def test_pdf_degrades_without_pdfplumber():
    """pdfplumber 打开失败时, fitz 仍应提取文字层, 不整体解析失败(回归)."""
    import fitz
    import pdfplumber
    in_dir = make_tmp_input()
    p = os.path.join(in_dir, "t.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((50, 72), "FALLBACKTEXT99")
    doc.save(p)
    doc.close()

    def _raise(*a, **k):
        raise RuntimeError("mock pdfplumber 失败")
    orig = pdfplumber.open
    pdfplumber.open = _raise
    try:
        from doc2md.parsers import pdf as pdfp
        r = pdfp.parse(p, {"output_root": None})
        assert_true("FALLBACKTEXT99" in r["body"],
                    f"fitz 文字应保留(降级提取), body: {r['body'][:200]!r}")
    finally:
        pdfplumber.open = orig
    cleanup(in_dir)


CASES.append(("pdf_degrades_without_pdfplumber", test_pdf_degrades_without_pdfplumber))
