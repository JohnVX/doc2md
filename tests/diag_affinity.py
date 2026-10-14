"""诊断: Windows OCR 阶段 CPU 到底占多少核 (不靠任务管理器, 用真实 CPU 时间).

先设 16 核亲和性, 然后用多线程跑真实 ONNX/OpenCV 工作, 通过
time.process_time() 测出进程实际用掉的 CPU 秒数, 除以墙钟得真实核数.
若亲和性生效, 结果必然 <= 16; 任务管理器显示的百分比只是 16*100/28=57%.

跑法: python tests/diag_affinity.py
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor

k = ctypes.WinDLL("kernel32", use_last_error=True)
k.GetCurrentProcess.restype = wintypes.HANDLE
k.GetCurrentProcess.argtypes = []
k.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
k.SetProcessAffinityMask.restype = wintypes.BOOL
k.GetProcessAffinityMask.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
k.GetProcessAffinityMask.restype = wintypes.BOOL

h = k.GetCurrentProcess()
cpu_count = os.cpu_count()
budget = max(2, int(cpu_count * 0.6))
mask = (1 << budget) - 1
ok = k.SetProcessAffinityMask(h, mask)
pa, sa = ctypes.c_size_t(), ctypes.c_size_t()
k.GetProcessAffinityMask(h, ctypes.byref(pa), ctypes.byref(sa))
proc_bits = bin(pa.value).count("1")
print(f"cpu_count={cpu_count} budget={budget} set_ok={bool(ok)} proc_bits={proc_bits}")
print(f"若限制生效: 进程最多用 {proc_bits} 核, 整机占比 <= {proc_bits*100//cpu_count}%")

# 用 numpy 做释放 GIL 的原生计算 (模拟 ONNX/OpenCV 的重计算)
try:
    import numpy as np
except ImportError:
    print("需要 numpy: pip install numpy")
    sys.exit(1)


def _work(_):
    # 矩阵乘 + FFT: 纯 C 实现, 不占 GIL, 能真并行
    a = np.random.rand(400, 400)
    b = np.random.rand(400, 400)
    t0 = time.time()
    n = 0
    while time.time() - t0 < 3.0:
        np.dot(a, b)
        np.fft.fft2(a)
        n += 1
    return n


for nthreads in (4, 12, 24):
    t_cpu0 = time.process_time()
    t_wall0 = time.time()
    with ThreadPoolExecutor(max_workers=nthreads) as pool:
        list(pool.map(_work, range(nthreads)))
    wall = time.time() - t_wall0
    cpu = time.process_time() - t_cpu0
    print(f"{nthreads:2d} 线程: wall={wall:.2f}s cpu={cpu:.2f}s "
          f"-> 实际用核 {cpu/wall:.2f} 核 (整机 {cpu/wall/cpu_count*100:.0f}%)")
