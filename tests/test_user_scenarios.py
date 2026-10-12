"""用户场景测试: 特殊文件名 / 无 OCR defer / 多 sheet xlsx / GBK 编码 / 代码文件 / verbose."""
import os

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            find_md_by_source, make_tmp_input, make_tmp_output,
                            parse_front_matter)
from tests import _samples

from doc2md import pipeline

CASES = []


def test_special_filenames():
    """文件名含空格、中文、括号 → 正常处理, 原件保留正确名."""
    in_dir = make_tmp_input({
        "my file.txt": "带空格的文件\n",
        "报告(最终版).txt": "带括号和中文\n",
    })
    out = make_tmp_output()
    try:
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        p, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        assert_eq(p, 2)
        mds = find_mds(out)
        assert_eq(len(mds), 2)
        assert_true(os.path.exists(os.path.join(out, "original-doc", "my file.txt")))
        assert_true(os.path.exists(os.path.join(out, "original-doc", "报告(最终版).txt")))
    finally:
        cleanup(in_dir, out)


def test_no_ocr_engine_defers():
    """OCR 引擎不可用时, 含图 PPTX → 图片 defer, 不崩溃."""
    import tempfile
    tmp = tempfile.mkdtemp()
    pptx_p = os.path.join(tmp, "img.pptx")
    _samples.gen_pptx_with_image(pptx_p)
    try:
        in_dir = make_tmp_input({"img.pptx": open(pptx_p, "rb").read()})
        out = make_tmp_output()
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        # 模拟无 OCR 引擎
        import doc2md.ocr as ocr_mod
        orig_available = ocr_mod.available
        orig_engine = ocr_mod._engine_obj
        ocr_mod.available = lambda: False
        ocr_mod._engine_obj = lambda: None
        try:
            p, _, e = pipeline.run(in_dir, out)
            assert_eq(e, 0, "无 OCR 不应报错")
            assert_eq(p, 1)
            md = find_md_by_source(out, "original-doc/img.pptx")
            assert_true("带图幻灯片" in md, "文字应正常提取")
            assert_true("stage2:" in md, "图片应 defer 到 stage2")
        finally:
            ocr_mod.available = orig_available
            ocr_mod._engine_obj = orig_engine
    finally:
        cleanup(tmp, in_dir, out)


def test_xlsx_multisheet():
    """多 sheet xlsx → 每 sheet 内容都出现在 md 中."""
    import tempfile
    tmp = tempfile.mkdtemp()
    xlsx_p = os.path.join(tmp, "multi.xlsx")
    _samples.gen_xlsx_multisheet(xlsx_p)
    try:
        in_dir = make_tmp_input({"multi.xlsx": open(xlsx_p, "rb").read()})
        out = make_tmp_output()
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        p, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        md = find_md_by_source(out, "original-doc/multi.xlsx")
        assert_true("汇总数据" in md, "第一个 sheet 内容应出现")
        assert_true("明细数据" in md, "第二个 sheet 内容应出现")
    finally:
        cleanup(tmp, in_dir, out)


def test_gbk_encoding():
    """GBK 编码 txt → 正确解码为 utf-8 输出."""
    tmp_gbk = os.path.join(os.path.dirname(__file__), "_tmp_gbk.txt")
    _samples.gen_gbk_txt(tmp_gbk)
    try:
        in_dir = make_tmp_input({"gbk_note.txt": open(tmp_gbk, "rb").read()})
        out = make_tmp_output()
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        p, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        md = find_md_by_source(out, "original-doc/gbk_note.txt")
        assert_true("中文GBK编码" in md, "GBK 内容应正确解码")
    finally:
        cleanup(in_dir, out)
        os.remove(tmp_gbk)


def test_code_file_output():
    """代码文件 → fenced code block + 语言标注."""
    in_dir = make_tmp_input({
        "CMakeLists.txt": "cmake_minimum_required(VERSION 3.16)\nproject(test)\n",
    })
    out = make_tmp_output()
    try:
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        p, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0)
        md = find_md_by_source(out, "original-doc/CMakeLists.txt")
        fm = parse_front_matter(md)
        assert_eq(fm["source_type"], "code")
        assert_true("```" in md, "应有代码围栏")
        assert_true("cmake" in md.lower(), "应有 cmake 语言标注")
    finally:
        cleanup(in_dir, out)


def test_verbose_mode():
    """-v verbose 模式 → 不崩溃, 正常处理."""
    in_dir = make_tmp_input({"note.txt": "verbose 测试\n"})
    out = make_tmp_output()
    try:
        pipeline.MAX_WORKERS = 1
        pipeline.MAX_OCR_THREADS = 1
        pipeline.BATCH_MODE = None
        p, _, e = pipeline.run(in_dir, out, verbose=True)
        assert_eq(e, 0)
        assert_eq(p, 1)
    finally:
        cleanup(in_dir, out)


CASES.extend([
    ("scenario_special_filenames", test_special_filenames),
    ("scenario_no_ocr_defer", test_no_ocr_engine_defers),
    ("scenario_xlsx_multisheet", test_xlsx_multisheet),
    ("scenario_gbk_encoding", test_gbk_encoding),
    ("scenario_code_file", test_code_file_output),
    ("scenario_verbose_mode", test_verbose_mode),
])
