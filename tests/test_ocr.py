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
