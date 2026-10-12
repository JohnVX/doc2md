"""OCR 模式决策测试: ProcessPool vs ThreadPool, 基于图数+硬件.

ProcessPool: batch + 图数≥50 + 内存够每进程 400MB.
worker 数 = min(CPU预算, 图数//10), 确保每 worker ≥10 图摊薄启动.
"""
from tests._runner import assert_eq, assert_true

from doc2md import pipeline

CASES = []


def test_few_images_thread():
    """20 图 → ThreadPool (启动开销不划算)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 20)
    assert_eq("thread", mode)


def test_threshold_50_process():
    """50 图, batch, 大内存 → ProcessPool."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 50)
    assert_eq("process", mode)
    assert_eq(5, n)  # 50//10=5


def test_streaming_always_thread():
    """50 图但 streaming → ThreadPool."""
    mode, n = pipeline._ocr_mode_decision(False, 1, 1700, 50)
    assert_eq("thread", mode)


def test_many_images_capped_by_cpu():
    """500 图, CPU 预算 12 → 12 worker (cap)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 500)
    assert_eq("process", mode)
    assert_eq(12, n)  # min(12, 500//10=50) = 12


def test_mem_limited_workers():
    """28核/4GB, 178 图 → 内存限制 worker 数."""
    # (4096-1024)//400 = 7, min(12, 17, 7) = 7
    mode, n = pipeline._ocr_mode_decision(True, 12, 4096, 178)
    assert_eq("process", mode)
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
    """50 图 → ProcessPool (刚好够)."""
    mode, n = pipeline._ocr_mode_decision(True, 12, 51200, 50)
    assert_eq("process", mode)


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
])
