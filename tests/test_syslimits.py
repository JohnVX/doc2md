"""系统级资源限制测试: CPU 亲和性 + 内存硬上限.

Windows: SetProcessAffinityMask + Job Object.
Linux: sched_setaffinity + RLIMIT_AS.
"""
import os
import sys

try:
    import resource  # 仅 Unix/Linux 可用
except ImportError:  # Windows
    resource = None

from tests._runner import assert_true, assert_eq, SkipTest

from doc2md import pipeline

CASES = []


def test_apply_system_limits_linux():
    """Linux: 设置 CPU 亲和性 + RLIMIT_AS, 验证生效."""
    if not sys.platform.startswith("linux"):
        raise SkipTest("仅 Linux")
    old_aff = os.sched_getaffinity(0)
    old_rlim = resource.getrlimit(resource.RLIMIT_AS)
    try:
        pipeline._apply_system_limits(1, os.cpu_count(), 4096)
        aff = os.sched_getaffinity(0)
        assert_true(0 in aff, f"CPU 0 应在亲和性集合中: {aff}")
        assert_true(len(aff) <= 1 or 1 in aff,
                     f"亲和性应限制到 1 核: {aff}")
        cur = resource.getrlimit(resource.RLIMIT_AS)
        assert_true(cur[0] > 0, f"RLIMIT_AS 应已设置: {cur}")
    finally:
        os.sched_setaffinity(0, old_aff)
        resource.setrlimit(resource.RLIMIT_AS, old_rlim)


def test_apply_system_limits_idempotent():
    """重复调用不报错."""
    if not sys.platform.startswith("linux"):
        raise SkipTest("仅 Linux")
    old_aff = os.sched_getaffinity(0)
    old_rlim = resource.getrlimit(resource.RLIMIT_AS)
    try:
        pipeline._apply_system_limits(2, os.cpu_count() or 2, 4096)
        pipeline._apply_system_limits(2, os.cpu_count() or 2, 8192)
    finally:
        os.sched_setaffinity(0, old_aff)
        resource.setrlimit(resource.RLIMIT_AS, old_rlim)


def test_apply_system_limits_zero_memory():
    """avail_mb=0 不崩溃 (防御性)."""
    if not sys.platform.startswith("linux"):
        raise SkipTest("仅 Linux")
    old_aff = os.sched_getaffinity(0)
    old_rlim = resource.getrlimit(resource.RLIMIT_AS)
    try:
        pipeline._apply_system_limits(2, os.cpu_count() or 2, 0)
    finally:
        os.sched_setaffinity(0, old_aff)
        resource.setrlimit(resource.RLIMIT_AS, old_rlim)


def test_get_affinity_count():
    """_get_affinity_count 返回亲和性限制后的核数."""
    if not sys.platform.startswith("linux"):
        raise SkipTest("仅 Linux")
    old_aff = os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0, {0})
        n = pipeline._get_affinity_count()
        assert_eq(1, n, f"亲和性设为 1 核, 但 _get_affinity_count 返回 {n}")
    finally:
        os.sched_setaffinity(0, old_aff)


CASES.extend([
    ("syslimits_linux", test_apply_system_limits_linux),
    ("syslimits_idempotent", test_apply_system_limits_idempotent),
    ("syslimits_zero_mem", test_apply_system_limits_zero_memory),
    ("syslimits_affinity_count", test_get_affinity_count),
])
