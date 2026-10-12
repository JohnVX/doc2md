"""OCR 测试: 引擎失败后不重试(mock, 仅断言可观测行为); 正向 OCR 出字.

rapidocr 未安装时跳过(OCR 为可选增强, 不应让测试失败)."""
from tests._runner import (HAS_RAPIDOCR, assert_contains_any, assert_eq,
                            assert_true, cleanup, make_tmp_input, require_ocr)

from doc2md import ocr

CASES = []


def test_engine_failure_cached():
    require_ocr()
    import rapidocr_onnxruntime as r
    orig_class = r.RapidOCR
    calls = {"n": 0}

    class BadEngine:
        def __init__(self):
            calls["n"] += 1
            raise RuntimeError("mock 引擎初始化失败")

        def __call__(self, *a, **k):
            raise RuntimeError("nope")

    # 复位 ocr 模块状态以强制重新 init (耦合内部状态, 但仅此一处)
    saved = (ocr._probed, ocr._engine, ocr._broken)
    ocr._probed, ocr._engine, ocr._broken = None, None, False
    r.RapidOCR = BadEngine
    try:
        assert_eq(ocr.ocr_image("/tmp/whatever.png"), "", "失败应返回空")
        assert_eq(calls["n"], 1, "第1次应尝试 init 一次")
        assert_eq(ocr.ocr_image("/tmp/whatever.png"), "", "第2次仍返回空")
        assert_eq(calls["n"], 1, "第2次不应再尝试 init")
    finally:
        r.RapidOCR = orig_class
        ocr._probed, ocr._engine, ocr._broken = saved


CASES.append(("engine_failure_cached", test_engine_failure_cached))


def test_available_returns_bool():
    # 环境无关: available() 不崩且返回布尔
    assert_true(isinstance(ocr.available(), bool))


CASES.append(("available_returns_bool", test_available_returns_bool))


def test_ocr_positive_produces_text():
    """正向: 含清晰文字的图片 -> OCR 应返回非空文本."""
    require_ocr()
    from tests import _samples
    tmp = make_tmp_input()
    png = tmp + "/t.png"
    _samples.gen_text_png(png, "OCRTEST123")
    txt = ocr.ocr_image(png)
    assert_true(bool(txt), "OCR 应产出非空文本")
    assert_contains_any(txt, ["ocr", "test", "123"], "OCR 结果应含原文片段")


CASES.append(("ocr_positive_produces_text", test_ocr_positive_produces_text))


def test_is_ocr_supported_wmf_emf():
    """WMF/EMF 格式不支持 OCR, PNG 支持."""
    assert_true(not ocr.is_ocr_supported(b'\xd7\xcd\xc6\x9a' + b'\x00' * 100), "WMF 不支持")
    emf = b'\x01\x00\x00\x00' + b'\x00' * 36 + b' EMF' + b'\x00' * 100
    assert_true(not ocr.is_ocr_supported(emf), "EMF 不支持")
    png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
    assert_true(ocr.is_ocr_supported(png), "PNG 支持")
    assert_true(not ocr.is_ocr_supported(b''), "空 blob 不支持")
    assert_true(not ocr.is_ocr_supported(None), "None 不支持")


CASES.append(("is_ocr_supported_wmf_emf", test_is_ocr_supported_wmf_emf))


def test_ocr_proc_mode_e2e():
    """ProcessPool OCR: 多进程并行, 结果正确."""
    require_ocr()
    from tests import _samples
    from doc2md import pipeline
    from concurrent.futures import ProcessPoolExecutor

    tmp = make_tmp_input()
    png1, png2 = tmp + "/a.png", tmp + "/b.png"
    _samples.gen_text_png(png1, "OCRPROC1")
    _samples.gen_text_png(png2, "OCRPROC2")

    import tempfile, os, shutil
    from pathlib import Path
    out_dir = tempfile.mkdtemp()
    adir = Path(out_dir) / "original-doc" / "doc-test_assets"
    adir.mkdir(parents=True)
    shutil.copy(png1, str(adir / "01_img.png"))
    shutil.copy(png2, str(adir / "02_img.png"))
    body = ("text before\n\n<!-- ocr:img:01_img.png -->\n\n"
            "middle\n\n<!-- ocr:img:02_img.png -->\n\ntext after\n")

    saved = (pipeline.OCR_MODE, pipeline.OCR_PROCS, pipeline._ocr_pool,
             pipeline.MAX_OCR_THREADS)
    pipeline.OCR_MODE = "process"
    pipeline.OCR_PROCS = 2
    pipeline._ocr_pool = ProcessPoolExecutor(
        max_workers=2, initializer=pipeline._ocr_proc_init)
    try:
        deferred_list = []
        result = pipeline._post_ocr(body, Path(out_dir), "doc-test", deferred_list)
        assert_true("```" in result, "应有 OCR 代码块")
        assert_true("OCRPROC" in result.upper() or not deferred_list,
                    "ProcessPool OCR 应产出文本或 defer")
    finally:
        pipeline._ocr_pool.shutdown(wait=True)
        pipeline._ocr_pool = None
        (pipeline.OCR_MODE, pipeline.OCR_PROCS, pipeline._ocr_pool,
         pipeline.MAX_OCR_THREADS) = saved
        shutil.rmtree(out_dir, ignore_errors=True)


CASES.append(("ocr_proc_mode_e2e", test_ocr_proc_mode_e2e))
