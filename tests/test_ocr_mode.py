"""OCR 模式决策测试: ProcessPool vs ThreadPool, 基于图数+硬件.

ProcessPool: Linux + batch + 图数≥50 + 内存够每进程 400MB.
Windows: 始终 ThreadPool (spawn 开销过高, 实测慢 3-5 倍).
worker 数 = min(CPU预算, 图数//10), 确保每 worker ≥10 图摊薄启动.
"""
import os
import sys
from pathlib import Path

from tests._runner import (assert_eq, assert_true, cleanup, make_tmp_output)

from doc2md import pipeline

CASES = []


def _process_or_thread_on_this_os():
    """平台相关期望: Windows 永远 thread (spawn 开销); 其他平台在满足条件时 process."""
    return "thread" if sys.platform == "win32" else "process"


def test_few_images_thread():
    """20 图 → ThreadPool (启动开销不划算)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 20)
    assert_eq("thread", mode)


def test_threshold_50_process():
    """50 图, batch, 大内存 → ProcessPool (Linux; Windows 为 thread)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 50)
    assert_eq(_process_or_thread_on_this_os(), mode)
    if sys.platform != "win32":
        assert_eq(5, n)  # 50//10=5


def test_streaming_always_thread():
    """50 图但 streaming → ThreadPool."""
    mode, n = pipeline._ocr_mode_decision(False, 1, 1700, 50)
    assert_eq("thread", mode)


def test_many_images_capped_by_cpu():
    """500 图, CPU 预算 12 → 12 worker (cap)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 500)
    assert_eq(_process_or_thread_on_this_os(), mode)
    if sys.platform != "win32":
        assert_eq(12, n)  # min(12, 500//10=50) = 12


def test_mem_limited_workers():
    """28核/4GB, 178 图 → 内存限制 worker 数."""
    # (4096-1024)//400 = 7, min(12, 17, 7) = 7
    mode, n = pipeline._ocr_mode_decision(True, 12, 4096, 178)
    assert_eq(_process_or_thread_on_this_os(), mode)
    if sys.platform != "win32":
        assert_eq(7, n)


def test_low_cpu_thread():
    """4核/4GB, 100 图 → max_ocr=1 < 2 → ThreadPool."""
    mode, n = pipeline._ocr_mode_decision(True, 1, 4096, 100)
    assert_eq("thread", mode)


def test_zero_images_thread():
    """0 图 → ThreadPool."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 0)
    assert_eq("thread", mode)


def test_mem_insufficient_thread():
    """28核/1.5GB, 100 图 → 非 batch → ThreadPool."""
    mode, n = pipeline._ocr_mode_decision(False, 1, 1500, 100)
    assert_eq("thread", mode)


def test_boundary_49_thread():
    """49 图 → ThreadPool (刚好不够 50)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 49)
    assert_eq("thread", mode)


def test_boundary_50_process():
    """50 图 → ProcessPool (刚好够, Linux; Windows thread)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 50)
    assert_eq(_process_or_thread_on_this_os(), mode)


def test_windows_always_thread():
    """Windows: 无论图数多少, 都用 ThreadPool (spawn 开销过高)."""
    old = sys.platform
    sys.platform = "win32"
    try:
        mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 500)
        assert_eq("thread", mode)
        assert_eq(12, n)
    finally:
        sys.platform = old


def test_defer_reason_consistent_across_paths():
    """回归: 图片 defer 的 note 在"缓存(批量预计算)"与"直算"路径下必须一致.

    真实缺陷: 并行 worker (_ocr_proc_worker / _precompute_ocr.work) 把
    "文件丢失""格式不支持""无文字"三种原因都压成空串, _post_ocr 无法区分,
    导致 batch(默认) 与 process 模式下 note 恒为空, 而流式路径却有原因.
    同一输入因内存量级不同而产出不同质量的 defer, stage2 丢失决策信息.
    修复: _post_ocr 拿到空文本时用 asset 路径就地重判原因.
    """
    out = make_tmp_output()
    try:
        doc_id = "doc-test"
        adir = os.path.join(out, "original-doc", f"{doc_id}_assets")
        os.makedirs(adir, exist_ok=True)
        # WMF 图: 顺序\x9a\xc6\xcd\xd7 为 magic (见 ocr.is_ocr_supported)
        wmf = b"\xd7\xcd\xc6\x9a" + b"\x00" * 100
        body = "text\n<!-- ocr:img:a.wmf -->\n"

        old_mode = pipeline.OCR_MODE
        old_cache = pipeline._ocr_text_cache
        try:
            pipeline.MAX_OCR_THREADS = 1
            pipeline._ocr_pool = None

            # 直算路径 (无缓存)
            with open(os.path.join(adir, "a.wmf"), "wb") as f:
                f.write(wmf)
            pipeline.OCR_MODE = "thread"
            pipeline._ocr_text_cache = {}
            dl_direct = []
            pipeline._post_ocr(body, Path(out), doc_id, dl_direct)

            # 缓存路径 (模拟预计算返回 None)
            pipeline._ocr_text_cache = {(doc_id, "a.wmf"): None}
            dl_cache = []
            pipeline._post_ocr(body, Path(out), doc_id, dl_cache)

            assert_eq(1, len(dl_direct), "直算路径应有 1 项 defer")
            assert_eq(1, len(dl_cache), "缓存路径应有 1 项 defer")
            assert_eq(dl_direct[0]["note"], dl_cache[0]["note"],
                      "两条路径的 note 必须一致")
            # 该 WMF 为合成的无效数据 (只有 magic, 无有效记录) -> 无人能渲染,
            # 故 note 应为"矢量图无法渲染"而非旧的"格式不支持" (语义更准: 是缺
            # 渲染能力, 不是永不支持 —— Windows GDI 能处理真 WMF).
            assert_true("无法渲染" in dl_cache[0]["note"],
                        "缓存路径必须给出具体原因 (缺渲染能力)")

            # 文件丢失 -> 另一种原因
            os.unlink(os.path.join(adir, "a.wmf"))
            dl_missing = []
            pipeline._post_ocr(body, Path(out), doc_id, dl_missing)
            assert_eq("资产文件丢失", dl_missing[0]["note"],
                      "文件丢失应给出对应原因, 而非空 note")
        finally:
            pipeline.OCR_MODE = old_mode
            pipeline._ocr_text_cache = old_cache
    finally:
        cleanup(out)


CASES.extend([
    ("ocr_mode_few_images_thread", test_few_images_thread),
    ("ocr_mode_threshold_50_process", test_threshold_50_process),
    ("ocr_mode_streaming_always_thread", test_streaming_always_thread),
    ("ocr_mode_many_images_capped", test_many_images_capped_by_cpu),
    ("ocr_mode_mem_limited", test_mem_limited_workers),
    ("ocr_mode_low_cpu_thread", test_low_cpu_thread),
    ("ocr_mode_zero_images", test_zero_images_thread),
    ("ocr_mode_mem_insufficient", test_mem_insufficient_thread),
    ("ocr_mode_boundary_49", test_boundary_49_thread),
    ("ocr_mode_boundary_50", test_boundary_50_process),
    ("ocr_mode_windows_always_thread", test_windows_always_thread),
    ("ocr_defer_reason_consistent", test_defer_reason_consistent_across_paths),
])
