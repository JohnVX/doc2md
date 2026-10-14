"""诊断: Windows CPU 占用率限制为何未生效.

跑法: python diag_cpu.py
会: 1) 读取当前 Job 状态 2) 确认进程是否已在 Job 中 3) 测量限速效果.
"""
import ctypes, os, sys, time, threading
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
k32.IsProcessInJob.restype = wintypes.BOOL

print("=== 1. 进程是否已在某个 Job 中 ===")
injob = wintypes.BOOL()
ok = k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(injob))
print(f"IsProcessInJob: ret={bool(ok)} inJob={bool(injob.value)}")
if injob.value:
    print("  !! 进程已在 Job 中 —— 若父 Job 未开嵌套, AssignProcessToJobObject 会失败")

print("\n=== 2. CPU rate 设置是否真生效 ===")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doc2md import pipeline
total = os.cpu_count() or 2
pipeline._apply_system_limits(max(2, int(total * 0.6)), total, 8192)
print(f"job_handle = {pipeline._job_handle}")

class CRC(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]
k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
k32.QueryInformationJobObject.restype = wintypes.BOOL
q = CRC(); ret = ctypes.c_ulong(0)
okq = k32.QueryInformationJobObject(pipeline._job_handle, 15, ctypes.byref(q), ctypes.sizeof(q), ctypes.byref(ret))
print(f"Query(15): ok={bool(okq)} err={ctypes.get_last_error()} flags={hex(q.ControlFlags)} rate={q.CpuRate}")
print(f"  -> 若 rate=0 或 flags=0, 说明 HARD_CAP 未真正写入")

print("\n=== 3. 亲和性 ===")
pa, sa = ctypes.c_size_t(), ctypes.c_size_t()
k32.GetProcessAffinityMask(k32.GetCurrentProcess(), ctypes.byref(pa), ctypes.byref(sa))
print(f"process affinity bits={bin(pa.value).count('1')}, system bits={bin(sa.value).count('1')}")

print("\n=== 4. 测速: 纯 CPU 忙循环 3 秒, 看实际能吃多少核 ===")
stop = time.time() + 3.0
count = [0]
lock = threading.Lock()
def worker():
    c = 0
    while time.time() < stop:
        c += 1
    with lock:
        count[0] += c
threads = [threading.Thread(target=worker) for _ in range(64)]
t0 = time.time()
for t in threads: t.start()
for t in threads: t.join()
elapsed = time.time() - t0
print(f"64 线程忙循环 {elapsed:.2f}s, 总迭代 {count[0]:,}")
print("  (占用率需另开任务管理器观察; 本步主要看是否被限速)")
