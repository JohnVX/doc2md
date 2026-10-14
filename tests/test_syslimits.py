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


def test_apply_system_limits_windows_cpu_rate():
    """Windows: Job Object CPU 占用率硬上限应设置成功(限核之外, 真限占用率).

    验证: 调用后读回 JobObjectCpuRateControlInformation, 确认 Enable|HardCap
    生效且 CpuRate 在合理范围.
    """
    if sys.platform != "win32":
        raise SkipTest("仅 Windows")
    import ctypes
    from ctypes import wintypes

    total = os.cpu_count() or 2
    pipeline._apply_system_limits(max(2, int(total * 0.8)), total, 8192)
    assert_true(pipeline._job_handle is not None, "Job Object 应创建成功")

    class _CpuRateControl(ctypes.Structure):
        _fields_ = [("ControlFlags", ctypes.c_uint32),
                    ("CpuRate", ctypes.c_uint32)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.c_void_p]
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    crc = _CpuRateControl()
    ret = ctypes.c_ulong(0)
    ok = k32.QueryInformationJobObject(
        pipeline._job_handle, 15, ctypes.byref(crc),
        ctypes.sizeof(crc), ctypes.byref(ret))
    assert_true(bool(ok), "QueryInformationJobObject(CPU rate) 应成功")
    ENABLE, HARD_CAP = 0x1, 0x4
    assert_true(bool(crc.ControlFlags & ENABLE), "CPU rate control 应启用")
    assert_true(bool(crc.ControlFlags & HARD_CAP), "应为 HARD_CAP 模式")
    assert_true(0 < crc.CpuRate <= 10000, f"CpuRate 应合理: {crc.CpuRate}")


def test_child_logging_setup():
    """回归: 子进程未配 logging 时应自配 handler (Windows spawn 必需).

    真实缺陷: Windows 用 spawn 启动 ProcessPool 子进程, 全新解释器不会重放父
    进程的 logging.basicConfig, 子进程 root logger 无 handler, 解析期日志
    (如 pdf 的 "PDF 检出 N 个跨页样板"/"PDF 加密且需口令") 被静默丢弃.
    Linux fork 继承 handler 故无此问题——同一命令两个平台日志不一致.
    """
    import logging
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_lvl = root.level
    saved_env = os.environ.get("DOC2MD_LOG_LEVEL")
    try:
        # 清空 handler, 模拟 spawn 子进程的初始状态
        for h in list(root.handlers):
            root.removeHandler(h)
        os.environ["DOC2MD_LOG_LEVEL"] = "DEBUG"
        pipeline._child_logging_setup()
        assert_true(len(root.handlers) > 0, "无 handler 时应自配 handler")
        assert_eq(logging.DEBUG, root.level, "级别应取自 DOC2MD_LOG_LEVEL")

        # 已有 handler (fork 继承): 不应重复配置
        n_before = len(root.handlers)
        pipeline._child_logging_setup()
        assert_eq(n_before, len(root.handlers), "已有 handler 时不应重复配置")
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
        root.setLevel(saved_lvl)
        if saved_env is None:
            os.environ.pop("DOC2MD_LOG_LEVEL", None)
        else:
            os.environ["DOC2MD_LOG_LEVEL"] = saved_env


CASES.extend([
    ("syslimits_linux", test_apply_system_limits_linux),
    ("syslimits_idempotent", test_apply_system_limits_idempotent),
    ("syslimits_zero_mem", test_apply_system_limits_zero_memory),
    ("syslimits_affinity_count", test_get_affinity_count),
    ("syslimits_windows_cpu_rate", test_apply_system_limits_windows_cpu_rate),
    ("syslimits_child_logging", test_child_logging_setup),
])
