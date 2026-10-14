"""诊断: 为何 Windows 上 CPU 亲和性/占用率限制未生效.

用与 pipeline._apply_system_limits 完全一致的 ctypes 签名调用,
打印每一步的真实返回值与 GetLastError. 定位后删除.

跑法: python tests/diag_affinity.py
"""
import ctypes
import sys
from ctypes import wintypes

k = ctypes.WinDLL("kernel32", use_last_error=True)
k.GetCurrentProcess.restype = wintypes.HANDLE
k.GetCurrentProcess.argtypes = []
k.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
k.SetProcessAffinityMask.restype = wintypes.BOOL
k.GetProcessAffinityMask.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
k.GetProcessAffinityMask.restype = wintypes.BOOL

h = k.GetCurrentProcess()
print("GetCurrentProcess handle =", h)

# 先读设置的"系统掩码"(进程最多能用哪些核)
pa, sa = ctypes.c_size_t(), ctypes.c_size_t()
ok = k.GetProcessAffinityMask(h, ctypes.byref(pa), ctypes.byref(sa))
print(f"[before] GetProcessAffinityMask ok={bool(ok)} err={ctypes.get_last_error()}")
print(f"[before] proc_bits={bin(pa.value).count('1')} sys_bits={bin(sa.value).count('1')}")

# 设置 16 核亲和性 (与代码一致)
mask = (1 << 16) - 1
ok = k.SetProcessAffinityMask(h, mask)
err = ctypes.get_last_error()
print(f"[set16] SetProcessAffinityMask(mask={mask:#x}) ok={bool(ok)} err={err}")

# 再读, 看是否真的变成 16
pa2, sa2 = ctypes.c_size_t(), ctypes.c_size_t()
ok2 = k.GetProcessAffinityMask(h, ctypes.byref(pa2), ctypes.byref(sa2))
print(f"[after]  GetProcessAffinityMask ok={bool(ok2)} err={ctypes.get_last_error()}")
print(f"[after]  proc_bits={bin(pa2.value).count('1')} sys_bits={bin(sa2.value).count('1')}")

# 是否已在某个 Job Object 中 (Windows 11 终端/某些环境会套 Job)
injob = wintypes.BOOL()
k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
k.IsProcessInJob.restype = wintypes.BOOL
k.IsProcessInJob(h, None, ctypes.byref(injob))
print("IsProcessInJob =", bool(injob.value))
