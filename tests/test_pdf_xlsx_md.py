"""PDF 表格提取 + xlsx 截断 + md 边界."""
import os

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output, require_ocr,
                            find_md_by_source)

from doc2md import pipeline

CASES = []


def test_pdf_table_extracted():
    """PDF 含表格 -> fitz 提取, md 有 markdown 表格."""
    import fitz
    import tempfile
    tmp = tempfile.mkdtemp()
    pdf_path = os.path.join(tmp, "table.pdf")
    doc = fitz.open()
    page = doc.new_page()
    shape = page.new_shape()
    # 画 2x2 表格边框
    for y in (50, 100, 150):
        shape.draw_line(fitz.Point(50, y), fitz.Point(250, y))
    for x in (50, 150, 250):
        shape.draw_line(fitz.Point(x, 50), fitz.Point(x, 150))
    shape.commit()
    page.insert_text((60, 80), "A1")
    page.insert_text((160, 80), "B1")
    page.insert_text((60, 130), "A2")
    page.insert_text((160, 130), "B2")
    doc.save(pdf_path)
    doc.close()
    try:
        in_dir = make_tmp_input({"table.pdf": open(pdf_path, "rb").read()})
        out = make_tmp_output()
        _, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        body = find_md_by_source(out, "original-doc/table.pdf")
        # 表格内容应在 md 中 (A1/B1/A2/B2)
        for cell in ("A1", "B1", "A2", "B2"):
            assert_true(cell in body, f"表格单元格 {cell} 应在 md 中")
        cleanup(in_dir, out)
    finally:
        cleanup(tmp)


CASES.append(("pdf_table_extracted", test_pdf_table_extracted))


def test_xlsx_row_truncation():
    """xlsx 超 300 行 -> 截断 + 提示."""
    import openpyxl
    import tempfile
    tmp = tempfile.mkdtemp()
    xlsx_p = os.path.join(tmp, "big.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BigSheet"
    ws.append(["c0", "c1", "c2"])
    for i in range(400):
        ws.append([f"r{i}c0", f"r{i}c1", f"r{i}c2"])
    wb.save(xlsx_p)
    wb.close()
    try:
        in_dir = make_tmp_input({"big.xlsx": open(xlsx_p, "rb").read()})
        out = make_tmp_output()
        _, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        body = open(find_mds(out)[0], encoding="utf-8").read()
        assert_true("截断" in body, "应有截断提示")
        cleanup(in_dir, out)
    finally:
        cleanup(tmp)


CASES.append(("xlsx_row_truncation", test_xlsx_row_truncation))


def test_md_remote_image_skipped():
    """md 含远程图片引用 -> 跳过, 不 OCR, 不 defer."""
    in_dir = make_tmp_input({"doc.md": "# Title\n\n![remote](https://example.com/img.png)\n\ntext\n"})
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    body = open(find_mds(out)[0], encoding="utf-8").read()
    assert_true("example.com" in body, "远程引用应保留")
    assert_true("stage2" not in body, "远程图片不应 defer")


CASES.append(("md_remote_image_skipped", test_md_remote_image_skipped))


def test_md_data_uri_skipped():
    """md 含 data URI 图片 -> 跳过."""
    in_dir = make_tmp_input({"doc.md": "# T\n\n![tiny](data:image/png;base64,iVBOR)\n\ntext\n"})
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    body = open(find_mds(out)[0], encoding="utf-8").read()
    assert_true("stage2" not in body, "data URI 不应 defer")


CASES.append(("md_data_uri_skipped", test_md_data_uri_skipped))


def test_md_no_h1_title_fallback():
    """md 无 H1 -> title 用文件名."""
    in_dir = make_tmp_input({"mydoc.md": "some content\nwithout heading\n"})
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    from tests._runner import parse_front_matter
    body = open(find_mds(out)[0], encoding="utf-8").read()
    fm = parse_front_matter(body)
    assert_eq(fm["title"], "mydoc", "无 H1 时 title 应用文件名(去扩展名)")


CASES.append(("md_no_h1_title_fallback", test_md_no_h1_title_fallback))
