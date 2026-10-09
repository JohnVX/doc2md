"""md 解析测试: 内嵌本地图片引用应被跟进(OCR + 拷到 assets)."""
import glob
import os

from tests import _samples
from tests._runner import (HAS_RAPIDOCR, assert_contains_any, assert_eq,
                            assert_true, cleanup, find_md_by_source,
                            make_tmp_input, make_tmp_output)

from doc2md import pipeline

CASES = []


def test_md_inline_local_image_followed():
    in_dir = make_tmp_input()
    os.makedirs(os.path.join(in_dir, "img"))
    png = os.path.join(in_dir, "img", "inline.png")
    _samples.gen_text_png(png, "INLINEOCR55")
    with open(os.path.join(in_dir, "doc.md"), "w", encoding="utf-8") as f:
        f.write("# 标题\n\n正文段落。\n\n![图](img/inline.png)\n")

    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    # 输入里 inline.png 也会被单独处理成图文档, 故按 source 定位 doc.md 的 md
    txt = find_md_by_source(out, "original-doc/doc.md")
    assert_true("_assets" in txt, f"内嵌图引用应改写指向 assets, 抽样: {txt[:200]!r}")
    assets = glob.glob(os.path.join(out, "original-doc", "*_assets", "*"))
    assert_true(len(assets) >= 1, "内嵌图应拷到 assets")
    if HAS_RAPIDOCR:
        assert_contains_any(txt, ["inline", "ocr", "55"], "内嵌图应 OCR 出文字")
    cleanup(in_dir, out)


CASES.append(("md_inline_local_image_followed", test_md_inline_local_image_followed))
