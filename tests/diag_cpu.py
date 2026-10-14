"""诊断: ONNX 引擎加载是否重置进程亲和性 (解释 OCR 阶段为何突破限制).

跑法: python tests/diag_cpu.py [--onnx]
  --onnx: 再加载 OCR 引擎并复测, 对比加载前后的亲和性与并行度.

观察点: 若加载 ONNX 后 process bits 从 16 变 28, 则 OpenMP/ONNX 重置了亲和性,
        这就是 OCR 阶段 CPU 冲到 85% 的根因.
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

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
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return sum(counter)


print("=== 1. 进程是否已在某个 Job 中 ===")
injob = wintypes.BOOL()
ok = k32.IsProcessInJob(cur_proc(), None, ctypes.byref(injob))
print(f"IsProcessInJob: ret={bool(ok)} inJob={bool(injob.value)}")

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

print("\n=== 3. 限制后的亲和性与并行度 ===")
print(f"affinity bits: {affinity_bits()}")
iters_before = run_busy(64)
print(f"64 线程 2s 迭代: {iters_before:,}")

if "--onnx" not in sys.argv:
    print("\n(加 --onnx 可测试加载 OCR 引擎后的变化)")
    sys.exit(0)

print("\n=== 4. 加载 ONNX 引擎 (复现 OCR 阶段) ===")
from doc2md import ocr
eng = ocr._engine_obj()
print(f"engine loaded: {eng is not None}")
print(f"affinity bits (引擎加载后): {affinity_bits()}")

print("\n=== 5. 引擎加载后的并行度 ===")
iters_after = run_busy(64)
print(f"64 线程 2s 迭代: {iters_after:,}")
print(f"变化: {iters_after - iters_before:+,}")

print("\n=== 6. ONNX session 实际线程数 ===")
try:
    import onnxruntime as ort
    print(f"ort.get_intra_op_num_threads: {ort.get_intra_op_num_threads()}")
except Exception as ex:
    print(f"(无法读取: {ex})")
try:
    so = eng.text_score.session.session.get_session_options() if hasattr(eng, 'text_score') else None
    print(f"sess_options intra_op: {so.intra_op_num_threads if so else 'n/a'}")
except Exception as ex:
    print(f"(session options 读取失败: {ex})")
