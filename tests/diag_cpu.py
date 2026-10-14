"""诊断: Windows CPU 占用率限制为何未生效.

跑法: python tests/diag_cpu.py
会: 1) 读取当前 Job 状态 2) 确认进程是否已在 Job 中 3) 测量限速效果.
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
# GetCurrentProcess 返回伪句柄 -1 (HANDLE)-1, 必须声明 restype 为 HANDLE,
# 且传入时用 ctypes.c_void_p(-1) 避免 Python int 溢出.
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
    """当前进程伪句柄, 用 c_void_p(-1) 避免溢出."""
    return ctypes.c_void_p(-1)


print("=== 1. 进程是否已在某个 Job 中 ===")
injob = wintypes.BOOL()
ok = k32.IsProcessInJob(cur_proc(), None, ctypes.byref(injob))
print(f"IsProcessInJob: ret={bool(ok)} inJob={bool(injob.value)}")

print("\n=== 2. CPU rate 设置是否真生效 ===")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from doc2md import pipeline
total = os.cpu_count() or 2
pipeline._apply_system_limits(max(2, int(total * 0.6)), total, 8192)
print(f"job_handle = {pipeline._job_handle}")


class CRC(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


q = CRC()
ret = ctypes.c_ulong(0)
okq = k32.QueryInformationJobObject(
    pipeline._job_handle, 15, ctypes.byref(q), ctypes.sizeof(q), ctypes.byref(ret))
print(f"Query(15): ok={bool(okq)} err={ctypes.get_last_error()} "
      f"flags={hex(q.ControlFlags)} rate={q.CpuRate}")

print("\n=== 3. 亲和性 ===")
pa, sa = ctypes.c_size_t(), ctypes.c_size_t()
ok = k32.GetProcessAffinityMask(cur_proc(), ctypes.byref(pa), ctypes.byref(sa))
print(f"GetProcessAffinityMask: ok={bool(ok)} "
      f"process bits={bin(pa.value).count('1')} system bits={bin(sa.value).count('1')}")

print("\n=== 4. 单核基线: 1 线程忙循环 2 秒 ===")


def busy(stop, counter, idx, lock):
    c = 0
    while time.time() < stop:
        c += 1
    with lock:
        counter[idx] = c


def run_busy(nthreads, seconds=2.0):
    stop = time.time() + seconds
    counter = [0] * nthreads
    lock = threading.Lock()
    ts = [threading.Thread(target=busy, args=(stop, counter, i, lock))
          for i in range(nthreads)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return time.time() - t0, sum(counter)


elapsed1, iters1 = run_busy(1)
print(f"1 线程: {elapsed1:.2f}s, {iters1:,} 迭代 (单核基线)")

print("\n=== 5. 64 线程忙循环 3 秒 (关键: 限速是否生效) ===")
elapsed64, iters64 = run_busy(64, 3.0)
ratio = iters64 / max(1, iters1) * (elapsed1 / elapsed64)
print(f"64 线程: {elapsed64:.2f}s, {iters64:,} 迭代")
print(f"有效并行度 ≈ {ratio:.1f} 核 (相对单核)")
print(f"  期望: 无限制≈{total} 核; HARD_CAP 57%≈{total * 0.57:.0f} 核; "
      f"亲和性 16 核≈16 核")
print("  若明显 >16, 说明亲和性未生效; 若 ≈16, 亲和性生效(CPU rate 与它等价)")
