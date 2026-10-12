"""PPTX 内容解析: 标题、正文、表格、备注 — 覆盖真实幻灯片场景."""
import os

from tests._runner import (assert_true, cleanup, find_md_by_source,
                            make_tmp_input, make_tmp_output)
from tests import _samples

from doc2md import pipeline

CASES = []


def test_pptx_rich_content():
    """PPTX 含标题+正文+表格+备注 → md 中全部出现."""
    import tempfile
    tmp = tempfile.mkdtemp()
    pptx_p = os.path.join(tmp, "rich.pptx")
    _samples.gen_pptx_rich(pptx_p)
    try:
        in_dir = make_tmp_input({"rich.pptx": open(pptx_p, "rb").read()})
        out = make_tmp_output()
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        p, _, e = pipeline.run(in_dir, out)
        assert_eq_check(e, 0, "不应有错误")
        assert_eq_check(p, 1, "应处理 1 文件")
        md = find_md_by_source(out, "original-doc/rich.pptx")
        assert_true("幻灯片标题" in md, "标题应出现")
        assert_true("正文内容段落" in md, "正文应出现")
        assert_true("表格幻灯片" in md, "第二张标题应出现")
        assert_true("表头A" in md and "表头B" in md, "表头应出现")
        assert_true("数据1" in md and "数据2" in md, "表格数据应出现")
        assert_true("备注内容" in md, "备注应出现")
    finally:
        cleanup(tmp, in_dir, out)


def assert_eq_check(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


CASES.extend([
    ("pptx_rich_content", test_pptx_rich_content),
])
