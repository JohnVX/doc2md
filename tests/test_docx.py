"""docx 解析测试: 表格单元格内的图片应被提取+OCR(回归 9 图全丢的 bug)."""
import os
import tempfile

from tests import _samples
from tests._runner import (HAS_RAPIDOCR, assert_contains_any, assert_eq,
                            assert_true, cleanup, find_md_by_source,
                            find_mds, make_tmp_input, make_tmp_output, read_md)

from doc2md import pipeline

CASES = []


def test_table_cell_image_extracted():
    # png 放输入目录外, 避免它被当独立图处理(只让 docx 作为输入)
    png_dir = tempfile.mkdtemp()
    png = os.path.join(png_dir, "cell.png")
    _samples.gen_text_png(png, "CELLOCR99")
    try:
        from docx import Document
        from docx.shared import Inches
        d = Document()
        d.add_paragraph("表格前的正文")
        table = d.add_table(rows=1, cols=1)
        table.cell(0, 0).paragraphs[0].add_run().add_picture(png, width=Inches(1))
        d.add_paragraph("表格后的正文")
        in_dir = make_tmp_input()
        d.save(os.path.join(in_dir, "with_table_img.docx"))

        out = make_tmp_output()
        _, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        assert_eq(len(find_mds(out)), 1, "只应有一个 md(仅 docx 作为输入)")
        txt = find_md_by_source(out, "original-doc/with_table_img.docx")
        assert_true("表格图" in txt, "md 应含表格图标记")
        if HAS_RAPIDOCR:
            assert_contains_any(txt, ["cell", "ocr", "99"], "OCR 应识别表格图文字")
        cleanup(in_dir, out)
    finally:
        cleanup(png_dir)


CASES.append(("table_cell_image_extracted", test_table_cell_image_extracted))


def test_ordered_list_rendered():
    """有序列表(List Number 样式)应渲染为 1. 而非 -. 嵌套应有缩进."""
    from docx import Document
    in_dir = make_tmp_input()
    p = os.path.join(in_dir, "list.docx")
    d = Document()
    d.add_paragraph("步骤一", style="List Number")
    d.add_paragraph("步骤二", style="List Number")
    d.add_paragraph("普通段")
    d.save(p)
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    txt = read_md(out)
    assert_true("1. 步骤一" in txt, f"有序列表应渲染为 '1. 步骤一', 抽样: {txt[:200]!r}")
    assert_true("1. 步骤二" in txt, "第二个有序项也应为 1. (markdown 自动编号)")
    cleanup(in_dir, out)


CASES.append(("ordered_list_rendered", test_ordered_list_rendered))


def test_image_only_paragraph_extracted():
    """纯图段落(无文字)的图也应被提取(回归图被'空段'判断提前 return 跳过的 bug)."""
    png_dir = tempfile.mkdtemp()
    png = os.path.join(png_dir, "p.png")
    _samples.gen_text_png(png, "ONLYIMG77")
    try:
        from docx import Document
        from docx.shared import Inches
        d = Document()
        d.add_paragraph().add_run().add_picture(png, width=Inches(1))  # 纯图段落, 无文字
        d.add_paragraph("图后的正文")
        in_dir = make_tmp_input()
        d.save(os.path.join(in_dir, "img_only.docx"))
        out = make_tmp_output()
        _, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        assert_eq(len(find_mds(out)), 1)
        txt = find_md_by_source(out, "original-doc/img_only.docx")
        assert_true("图1" in txt, f"纯图段落的图应提取, 抽样: {txt[:200]!r}")
        if HAS_RAPIDOCR:
            assert_contains_any(txt, ["only", "img", "77"], "纯图段落应 OCR")
        cleanup(in_dir, out)
    finally:
        cleanup(png_dir)


CASES.append(("image_only_paragraph_extracted", test_image_only_paragraph_extracted))
