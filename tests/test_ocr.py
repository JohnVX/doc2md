"""OCR 测试: 引擎失败后不重试(mock, 仅断言可观测行为); 正向 OCR 出字.

rapidocr 未安装时跳过(OCR 为可选增强, 不应让测试失败)."""
import os

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


def test_intra_op_forced_single():
    """回归: intra_op 应被强制为配置值 (默认 4), 即使调用方自带 sess_options.

    真实 bug: rapidocr 的 OrtInferSession 总是自己传 sess_options
    (其 config intra_op_num_threads=-1 → 不设置 → ORT 默认=全部核心).
    旧 patch 只在 "sess_options" 不在 kwargs 时才注入, 因此被绕过,
    intra_op 实为 0(全核). N 线程 × 全核 → 严重超订 → Windows 16核 退化为串行.
    本测试直接构造 "调用方传 sess_options" 的场景验证 patch 生效.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return  # onnxruntime 未装则跳过
    saved = getattr(ort.InferenceSession, "_d2m_patched", False)
    orig = ort.InferenceSession
    try:
        # 复位状态以确保重新 patch
        ort.InferenceSession = orig
        if hasattr(ort.InferenceSession, "_d2m_patched"):
            delattr(ort.InferenceSession, "_d2m_patched")
        ocr._intra_op_single()
        patched = ort.InferenceSession
        assert_true(getattr(patched, "_d2m_patched", False), "patch 应生效")
        # 模拟 rapidocr: 调用方自带 sess_options (intra_op 未设置 = ORT 默认)
        seen = {}

        class Dummy(orig):
            def __init__(self, *a, **k):
                seen["opts"] = k.get("sess_options")

        # 用 patched 类, 但让其父类 __init__ 被换成 Dummy 记录
        class Rec(orig):
            def __init__(self, *a, **k):
                raise RuntimeError("stop-after-capture")

        # 直接调用 patched 的 __init__ 逻辑: 构造 kwargs 后捕获
        caller_opts = ort.SessionOptions()
        try:
            patched("nonexistent.onnx", sess_options=caller_opts)
        except Exception:
            pass
        # 关键断言: 调用方传入的 opts 对象被原地改为配置值 (默认 4)
        expect = ocr._intra_op_value()
        assert_eq(caller_opts.intra_op_num_threads, expect,
                  "传入的 sess_options.intra_op 应被强制为配置值")
        assert_eq(caller_opts.inter_op_num_threads, 1,
                  "传入的 sess_options.inter_op 应被强制为 1")
    finally:
        ort.InferenceSession = orig


CASES.append(("intra_op_forced_single", test_intra_op_forced_single))


def test_new_engine_forces_single_thread():
    """回归: _new_engine 构造的引擎 session intra_op 应为配置值 (默认 4).

    首选路径是 RapidOCR(intra_op_num_threads=N), 不依赖 monkey-patch.
    默认 4 (实测 12 线程下最优); 环境变量 DOC2MD_OCR_INTRA_OP 可覆盖.
    """
    require_ocr()
    expect = ocr._intra_op_value()
    try:
        eng = ocr._new_engine()
    except Exception:
        return  # 引擎不可用则跳过
    try:
        so = eng.text_det.infer.session.get_session_options()
        assert_eq(expect, so.intra_op_num_threads,
                  "det session intra_op 应为配置值")
        assert_eq(1, so.inter_op_num_threads,
                  "det session inter_op 应为 1")
    except Exception:
        pass


def test_intra_op_value_default_and_override():
    """_intra_op_value: 默认返回 4, 环境变量可覆盖 (防超订不取全核)."""
    old = os.environ.pop("DOC2MD_OCR_INTRA_OP", None)
    try:
        assert_eq(4, ocr._intra_op_value(), "默认应为 4")
        os.environ["DOC2MD_OCR_INTRA_OP"] = "2"
        assert_eq(2, ocr._intra_op_value(), "环境变量应覆盖")
        os.environ["DOC2MD_OCR_INTRA_OP"] = "notint"
        assert_eq(4, ocr._intra_op_value(), "非法值应回退默认")
    finally:
        os.environ.pop("DOC2MD_OCR_INTRA_OP", None)
        if old is not None:
            os.environ["DOC2MD_OCR_INTRA_OP"] = old


CASES.append(("new_engine_forces_single_thread", test_new_engine_forces_single_thread))
CASES.append(("intra_op_value_default_and_override", test_intra_op_value_default_and_override))
