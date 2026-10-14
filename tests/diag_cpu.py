"""诊断: Windows CPU 占用率限制为何未生效 (修正版).

关键修正: 纯 Python 忙循环受 GIL 限制, 无论多少线程都只能达 ~1 核,
故不能用来测并行度. 本脚本改用 '释放 GIL 的原生计算' 来测实际并行度.

跑法:
  python tests/diag_cpu.py              # 仅环境与设置检查
  python tests/diag_cpu.py --probe      # 加测: 真实并行度(跨进程, 不受 GIL 限制)
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes
from multiprocessing import Pool, cpu_count

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.GetCurrentProcess.argtypes = []
k32.IsProcessInJob.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
k32.IsProcessInJob.restype = wintypes.BOOL
k32.GetProcessAffinityMask.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
k32.GetProcessAffinityMask.restype = wintypes.BOOL
k32.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
k32.QueryInformationJobObject.restype = wintypes.BOOL


def cur_proc():
    return ctypes.c_void_p(-1)


def affinity_bits():
    pa, sa = ctypes.c_size_t(), ctypes.c_size_t()
    ok = k32.GetProcessAffinityMask(cur_proc(), ctypes.byref(pa), ctypes.byref(sa))
    return (bin(pa.value).count("1"), bin(sa.value).count("1")) if ok else (None, None)


def _spin(seconds):
    """原生忙循环: 用 float 数组求和, 每秒调用, 部分释放 GIL.

    真正并行度应改用 os.times()/time.process_time 测 CPU 时间, 而非线程数.
    """
    import hashlib
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        hashlib.sha256(str(n).encode()).digest()
        n += 1
    return n


def measure_cpu_time(fn, *args):
    """返回 (wall, cpu) —— cpu 是进程占用的总 CPU 秒, 据此算真实并行度."""
    t_cpu0 = time.process_time()
    t_wall0 = time.time()
    fn(*args)
    wall = time.time() - t_wall0
    cpu = time.process_time() - t_cpu0
    return wall, cpu


print("=== 1. 环境 ===")
injob = wintypes.BOOL()
k32.IsProcessInJob(cur_proc(), None, ctypes.byref(injob))
print(f"inJob={bool(injob.value)}")
print(f"cpu_count={cpu_count()}")

print("\n=== 2. 应用系统限制 ===")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from doc2md import pipeline
total = os.cpu_count() or 2
pipeline._apply_system_limits(max(2, int(total * 0.6)), total, 8192)
print(f"job_handle = {pipeline._job_handle}")


class CRC(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


q = CRC()
ret = ctypes.c_ulong(0)
k32.QueryInformationJobObject(
    pipeline._job_handle, 15, ctypes.byref(q), ctypes.sizeof(q), ctypes.byref(ret))
print(f"CPU rate: flags={hex(q.ControlFlags)} rate={q.CpuRate}")
print(f"affinity bits: {affinity_bits()}")

print("\n=== 3. 单核基线 (原生计算 2s) ===")
wall1, cpu1 = measure_cpu_time(_spin, 2.0)
print(f"wall={wall1:.2f}s cpu={cpu1:.2f}s -> 并行度 {cpu1 / wall1:.2f}")

if "--probe" not in sys.argv:
    print("\n(加 --probe 测多进程真实并行度)")
    sys.exit(0)

print("\n=== 4. 多进程真实并行度 (12 进程, 各原生计算 5s) ===")
print("  说明: 子进程继承 Job + 亲和性; 若限制生效, 总 CPU 时间应 <= 16核*5s=80s")


def _child(_):
    t0 = time.time()
    import hashlib
    n = 0
    while time.time() - t0 < 5.0:
        hashlib.sha256(str(n).encode()).digest()
        n += 1
    return os.getpid()


t_wall0 = time.time()
with Pool(12) as p:
    p.map(_child, range(12))
wall = time.time() - t_wall0
print(f"12 进程耗时 wall={wall:.2f}s")
print("  若限制生效: wall 应明显 >5s (被限速) 或 >5s 且整机占用<=57%")
print("  若未生效: wall ≈5s (12 进程真并行, 12 核 <= 16 核上限)")
