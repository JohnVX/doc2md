"""主流程: 扫描输入 -> 识别 -> [解析+OCR+写出] -> 清理+索引.

三阶段: serial 预处理(检测/sha/跳过/重分类) -> 解析+收尾 -> 清理+索引.
两种流水线模式 (按可用内存自动选择, 无需人工配置):
  - batch (≥4GB): 全部解析 → shutdown pool → OCR+写出. worker 与 OCR 不重叠, CPU 不叠加.
  - streaming (<4GB): 解析一个 → 立即 OCR+写出+gc.collect(). 低内存安全.
两种 OCR 并行模式 (解析后按图数+硬件自动决策):
  - ProcessPool (图≥50 + batch + 多核 + 内存够): 多进程独立引擎, 无 ONNX session 锁.
    worker 数 = min(CPU预算, 图数//10), 每进程 ~400MB.
  - ThreadPool (其余): 共享单实例引擎, ONNX run() 释放 GIL 提供部分并行.
解析用 ProcessPool 而非 ThreadPool: fitz.Page.find_tables() 在 C 层有全局状态, 线程不安全.
系统级限制 (硬上限, 子进程继承): CPU亲和性→80%核心, Win:Job Object内存/Linux:RLIMIT_AS.
应用级配置 (优化): 读亲和性后实际核数; 解析进程上限 8, OCR 线程 min(核数-1, 12).
parser 只存图+插占位 <!-- ocr:type:dest -->, _post_ocr 替换占位为 OCR 文本或 defer 标记.
parser 返回的 deferred (结构化 stage2 待处理项) 经 aggregate_deferred 合并后
写入 front-matter / manifest / catalog, 供 stage2 agent 编程查询.
"""
import gc
import logging
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import yaml

from . import catalog, denoise, detector, manifest, ocr, parsers, util
from .config import load_config
from .parsers._common import aggregate_deferred, defer, guess_image_class

log = logging.getLogger("pipeline")

LARGE_FILE_MB = 50  # 超过此值给出慢速警告
MAX_WORKERS = None   # None=自动嗅探; 测试可设为 1 强制串行
MAX_OCR_THREADS = None  # None=自动嗅探
BATCH_MODE = None  # None=自动嗅探; True/False 可测试强制
OCR_MODE = None  # None=自动(解析后决策); "process"/"thread" 可测试强制
OCR_PROCS = None  # None=自动; ProcessPool 进程数 (process 模式)
_CPUS = 0  # 嗅探到的实际核数 (日志用)
_AVAIL_MB = 0  # 嗅探到的可用内存 MB (日志/决策用)

_OCR_RE = re.compile(r'<!-- ocr:(img|scan):(\S+) -->')
_ocr_pool = None  # ProcessPool for OCR (process 模式, 惰性创建, pipeline 结束后 shutdown)
_ocr_text_cache = {}  # (doc_id, dest) -> OCR 文本 (跨文档全局预计算; thread 模式)
_job_handle = None  # Windows Job Object handle (防止 GC 释放)


def _ocr_mode_decision(batch_mode, max_ocr, avail_mb, total_images):
    """决定 OCR 并行模式: ProcessPool 还是 ThreadPool.

    ProcessPool: 各进程独立 GIL+引擎, 无 ONNX session 锁, 预处理+推理真并行.
    条件 (全部满足):
      - Linux (Windows spawn 开销过高, 实测慢 3-5 倍)
      - batch 模式
      - 图数 ≥ 50 (启动开销需摊薄, 每 worker ≥10 图)
      - max_ocr ≥ 2 (单进程无并行收益)
      - 内存够每个进程 400MB 引擎 (avail_mb ≥ workers×400 + 1024)
    worker 数 = min(max_ocr, total_images//10, (avail_mb-1024)//400)
    返回 (mode, workers).
    """
    if sys.platform == "win32":
        return "thread", max_ocr  # Windows spawn 开销过高, ThreadPool 更快
    OCR_THRESHOLD = 50
    IMAGES_PER_WORKER = 10
    if not batch_mode or total_images < OCR_THRESHOLD or max_ocr < 2:
        return "thread", max_ocr
    proc_budget = max(0, (avail_mb - 1024) // 400)
    workers = min(max_ocr, max(2, total_images // IMAGES_PER_WORKER), proc_budget)
    if workers < 2:
        return "thread", max_ocr
    return "process", workers


def _ocr_proc_init():
    """ProcessPool worker 初始化: 加载 ONNX 引擎, 计时."""
    import time
    _child_logging_setup()
    t0 = time.time()
    eng = ocr._engine_obj()
    elapsed = time.time() - t0
    if eng is not None:
        log.info("OCR worker %d 引擎就绪 (%.1fs)", os.getpid(), elapsed)
    else:
        log.warning("OCR worker %d 引擎加载失败 (%.1fs)", os.getpid(), elapsed)


def _ocr_proc_worker(asset_path_str):
    """ProcessPool OCR worker: 读图→检测格式→OCR. 返回文本或 ''(defer).

    空串不区分原因(缺失/不支持/无文字); 调用方 _post_ocr 用 asset 路径重判, 故
    worker 与父进程间无需传原因 (简化跨进程负载).
    """
    from pathlib import Path
    p = Path(asset_path_str)
    if not p.exists():
        return ""
    blob = p.read_bytes()
    if not ocr.is_ocr_supported(blob):
        return ""
    return ocr.ocr_image(blob)


def _ocr_asset_text(m, adir):
    """单张图 OCR (直算路径): 缺失/不支持的图返回 '' (具体原因由 _post_ocr 重判)."""
    asset_path = adir / m.group(2)
    if not asset_path.exists():
        return ""
    try:
        blob = asset_path.read_bytes()
    except Exception:
        return ""
    if not ocr.is_ocr_supported(blob):
        return ""
    return ocr.ocr_image(blob)


def _get_affinity_count():
    """获取亲和性限制后的实际可用 CPU 核心数 (None = 不支持/失败)."""
    try:
        if sys.platform == "win32":
            import ctypes
            pa = ctypes.c_size_t()
            sa = ctypes.c_size_t()
            ctypes.windll.kernel32.GetProcessAffinityMask(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(pa), ctypes.byref(sa))
            return bin(pa.value).count('1')
        elif sys.platform.startswith("linux"):
            return len(os.sched_getaffinity(0))
    except Exception:
        pass
    return None


def _probe_avail_mb():
    """返回可用物理内存 MB (psutil → 平台 API → 2048 保守默认)."""
    avail_mb = 2048  # 保守默认
    try:
        import psutil
        avail_mb = psutil.virtual_memory().available // (1024 * 1024)
        return avail_mb
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            ms = _MemStatus()
            ms.dwLength = ctypes.sizeof(ms)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            avail_mb = ms.ullAvailPhys // (1024 * 1024)
        except Exception:
            pass
    else:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        avail_mb = int(line.split()[1]) // 1024
                        break
        except Exception:
            pass
    return avail_mb


def _detect_resources():
    """嗅探硬件 + 设置系统级限制, 返回 (max_workers, max_ocr, cpus, avail_mb, batch_mode).

    两层限制 (互补, 不可省任一):
    - 系统级 (硬上限): CPU 亲和性→80%核心 (DOC2MD_CPU_PCT 可调), Job Object→60%内存.
      子进程继承, ONNX/OpenCV 内部线程也无法绕过. 这是安全网.
    - 应用级 (优化): 读亲和性限制后的实际核数, 决定 spawn 几个进程.
      避免进程数>核数导致的上下文切换. 这是吞吐优化.

    batch (≥4GB): 全部解析→shutdown pool→OCR. 解析与 OCR 是先后两阶段,
                  各自按自己的预算算 (不互相挤占).
    streaming (<4GB): 解析一个→立即 OCR+写出+gc. OCR 拿满, worker=1.

    CPU 上限可调 (环境变量):
      DOC2MD_CPU_PCT  整机占用上限百分比 (默认 80). 同时决定亲和性核数与
                      Job Object CPU rate.
      DOC2MD_OCR_THREADS  OCR 线程数 (默认 min(核数-1, 12), 实测 12 最优).
    """
    raw_cpus = os.cpu_count() or 2

    avail_mb = _probe_avail_mb()

    log.info("硬件: %d 核 / %.1fGB 可用", raw_cpus, avail_mb / 1024)

    # 系统级限制: CPU 上限 DOC2MD_CPU_PCT% (默认 80) + 60% 内存 (硬上限, 子进程继承)
    try:
        cpu_pct = float(os.environ.get("DOC2MD_CPU_PCT", "80"))
    except ValueError:
        cpu_pct = 80.0
    cpu_pct = min(100.0, max(1.0, cpu_pct))
    cpu_budget = max(1, int(raw_cpus * cpu_pct / 100))
    _apply_system_limits(cpu_budget, raw_cpus, avail_mb)

    # 应用级: 读亲和性限制后的实际核数 (不再自己打折, 亲和性已限制)
    cpus = _get_affinity_count() or cpu_budget
    ocr_budget_mb = max(0, int(avail_mb * 0.6) - 1024)
    batch_mode = avail_mb >= 4096
    forced_ocr = os.environ.get("DOC2MD_OCR_THREADS")
    if batch_mode:
        # 解析与 OCR 是**先后两个阶段** (解析全部完成后 shutdown pool 才开 OCR),
        # 故各自按满预算算, 不互相挤占. 旧式 cpus-max_ocr 会让 OCR 大时解析只剩
        # 1 进程 (实测解析 6.0s -> 11.8s), 无谓拖慢.
        # 解析: 进程数受核数/内存/文件数限, 上限 8 (防 spawn 开销与内存暴涨).
        max_workers = min(cpus, max(1, avail_mb // 500), 8)
        # OCR: 默认不超过 12 线程. 实测 (Windows 28核, 156 图) 扫描:
        #    4线程=166.2s | 8=107.5s | 12=91.8s | 16=97.7s | 21=101.1s
        # 12 为最优: 更少核数不足, 更多超订 (上下文切换 / ONNX session 锁 /
        # 内存带宽饱和). 保留 DOC2MD_OCR_THREADS 可现场覆盖调优.
        # 注意: 真正决定速度的是**总并发 = 本线程数 x intra_op** (默认 intra_op=1).
        # 乘积 12~16 是实测最优平台, 与怎么拆无关 (6x2=12 与 12x1=12 同速);
        # 调大 DOC2MD_OCR_INTRA_OP 时需同步调小本值, 否则乘积超过 16 反而变慢.
        max_ocr = min(max(1, cpus - 1), 12)
        if forced_ocr:
            try:
                max_ocr = max(1, int(forced_ocr))
            except ValueError:
                pass
        max_ocr = min(max_ocr, max(1, ocr_budget_mb // 200))
    else:
        max_ocr = min(max(1, cpus), max(1, ocr_budget_mb // 200))
        if forced_ocr:
            try:
                max_ocr = max(1, int(forced_ocr))
            except ValueError:
                pass
        max_workers = 1
    log.info("配置: 解析 %d 进程, OCR %d 线程 (%s)",
             max_workers, max_ocr, "批量" if batch_mode else "流式")
    return max_workers, max_ocr, cpus, avail_mb, batch_mode


def _apply_system_limits(cpu_budget, cpus, avail_mb):
    """从系统层面限制 CPU 和内存 (子进程自动继承).

    Windows:
      1. CPU 亲和性: SetProcessAffinityMask → 只用 cpu_budget 个核心.
         子进程继承掩码, ONNX/OpenCV 内部线程也无法绕过.
      2. Job Object: 内存硬上限 (整个进程树 commit 总量, 超限则分配失败).
      3. Job Object CPU 占用率硬上限 (Win8+): 按整机百分比 cpu_pct 计, 子进程继承.
         亲和性只限核数(限住的核仍可被跑满), 本项才限真正的 CPU 占用率.
    Linux:
      1. CPU 亲和性: sched_setaffinity.
      2. RLIMIT_AS: 单进程虚拟地址空间上限 (防泄漏, 非 tree-wide; tree-wide 需 cgroups/root).
    """
    global _job_handle
    parts = []

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        k32.SetProcessAffinityMask.restype = wintypes.BOOL
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL

        hProc = k32.GetCurrentProcess()

        # 1. CPU 亲和性
        try:
            mask = (1 << cpu_budget) - 1
            if k32.SetProcessAffinityMask(hProc, mask):
                parts.append(f"CPU亲和={cpu_budget}/{cpus}核")
            else:
                log.warning("CPU亲和性失败 (GetLastError=%d)", ctypes.get_last_error())
        except Exception as ex:
            log.warning("CPU亲和性异常: %s", ex)

        # 2. Job Object: 内存硬上限 (整个进程树)
        try:
            hJob = k32.CreateJobObjectW(None, None)
            if not hJob:
                log.warning("CreateJobObject失败 (GetLastError=%d)", ctypes.get_last_error())
            else:
                class _IO_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong),
                    ]

                class _BasicLimit(ctypes.Structure):
                    _fields_ = [
                        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("Priority", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD),
                    ]

                class _ExtendedLimit(ctypes.Structure):
                    _fields_ = [
                        ("BasicLimitInformation", _BasicLimit),
                        ("IoInfo", _IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t),
                    ]

                JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
                JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
                mem_limit = int(avail_mb * 0.6) * 1024 * 1024  # bytes

                ext = _ExtendedLimit()
                ext.BasicLimitInformation.LimitFlags = (
                    JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY)
                ext.ProcessMemoryLimit = mem_limit
                ext.JobMemoryLimit = mem_limit

                ok1 = k32.SetInformationJobObject(
                    hJob, 9, ctypes.byref(ext), ctypes.sizeof(ext))
                ok2 = k32.AssignProcessToJobObject(hJob, hProc) if ok1 else False
                if ok1 and ok2:
                    _job_handle = hJob
                    mem_gb = int(avail_mb * 0.6) / 1024
                    parts.append(f"内存硬上限={mem_gb:.0f}GB")
                else:
                    err = ctypes.get_last_error()
                    step = "SetInformationJobObject" if not ok1 else "AssignProcessToJobObject"
                    log.warning("%s失败 (GetLastError=%d)", step, err)

                # 3. CPU 占用率硬上限 (JobObjectCpuRateControlInformation, Win8+).
                # 亲和性只限“用哪几个核”, 这 N 个核仍会被跑满; 本项才真正限制
                # 整个进程树的 CPU **占用率** (按系统总 CPU 的百分比计).
                # ControlFlags = ENABLE(0x1) | HARD_CAP(0x4); CpuRate 单位为 1/100 百分比.
                if ok1 and ok2:
                    try:
                        # JOBOBJECT_CPU_RATE_CONTROL_INFORMATION:
                        #   DWORD ControlFlags; union { DWORD CpuRate; DWORD Weight; };
                        # 长度必须精确为 8 字节, 否则 SetInformationJobObject
                        # 报 ERROR_BAD_LENGTH(24) 而失败.
                        # 用 c_uint32 而非 wintypes.DWORD: 后者在非 Windows 平台
                        # 退化为 8 字节, 显式指定 4 字节才能保证结构布局与 C 一致.
                        class _CpuRateControl(ctypes.Structure):
                            _fields_ = [("ControlFlags", ctypes.c_uint32),
                                        ("CpuRate", ctypes.c_uint32)]

                        JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
                        JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
                        # rate 与亲和性同口径 (均基于取整后的 cpu_budget), 避免两者
                        # 不一致而互相打架. 故实设比例略低于 DOC2MD_CPU_PCT 标称值
                        # (如 80% -> 22/28 = 78.6%), 取决于核数取整.
                        rate = max(1, min(10000, int(10000 * cpu_budget / max(1, cpus))))
                        crc = _CpuRateControl()
                        crc.ControlFlags = (JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                                            | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP)
                        crc.CpuRate = rate
                        # InfoClass 15 = JobObjectCpuRateControlInformation
                        ok3 = k32.SetInformationJobObject(
                            hJob, 15, ctypes.byref(crc), ctypes.sizeof(crc))
                        if ok3:
                            parts.append(f"CPU占用上限={rate / 100:.0f}%")
                        else:
                            log.warning("CPU 占用率限制失败 (GetLastError=%d); "
                                        "仅亲和性生效 (限核不限占用)",
                                        ctypes.get_last_error())
                    except Exception as ex:
                        log.warning("CPU 占用率限制异常: %s", ex)

        except Exception as ex:
            log.warning("Job Object 异常: %s", ex)

    elif sys.platform.startswith("linux"):
        try:
            os.sched_setaffinity(0, set(range(cpu_budget)))
            parts.append(f"CPU亲和={cpu_budget}/{cpus}核")
        except Exception:
            pass
        try:
            import resource
            mem_per_proc = max(4096, int(avail_mb * 0.6))  # MB, 至少 4GB/进程
            mem_bytes = mem_per_proc * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            parts.append(f"内存上限={mem_per_proc}MB/进程")
        except Exception:
            pass

    if parts:
        log.info("系统限制: %s", ", ".join(parts))


def _precompute_ocr(jobs, out):
    """跨文档全局 OCR 预计算 (thread 模式): 一个池跑完所有文档的所有图片.

    jobs: [(doc_id, dest, asset_path_str), ...] 全量 OCR 任务.
    结果写入 _ocr_text_cache[(doc_id, dest)] = 文本或 None(空结果显示/跳过).
    None 不区分原因(缺失/不支持/无文字); _post_ocr 消费时用 asset 路径重判,
    保证与直算路径 note 一致 (见 test_ocr_mode::ocr_defer_reason_consistent).

    为何要全局池: 逐文档新建 ThreadPool 会让每个文档末尾出现并行度空洞
    (文档图数 < 线程数时尾部跑不满), 文档越多空洞越明显. 全局池把全部图
    摊平到同一组线程上, 消除空洞. 实测 (Linux 2核, 真实图集) 150.3s -> 135.1s
    (+10%); 空洞随核数放大, 高核机器收益更大.

    图片读取/decode 都在 worker 内完成 (与逐文档路径一致).
    """
    global _ocr_text_cache
    if not jobs:
        return
    workers = min(MAX_OCR_THREADS, len(jobs))

    def work(job):
        doc_id, dest, asset_path = job
        p = Path(asset_path)
        if not p.exists():
            return None
        try:
            blob = p.read_bytes()
        except Exception:
            return None
        if not ocr.is_ocr_supported(blob):
            return None
        txt = ocr.ocr_image(blob)
        return txt or None

    log.info("OCR 预计算: %d 图, 全局 ThreadPool %d 线程", len(jobs), workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, jobs))
    _ocr_text_cache = {(doc_id, dest): txt
                       for (doc_id, dest, _), txt in zip(jobs, results)}


def _post_ocr(body, out, doc_id, deferred_list):
    """收尾 OCR: 替换 <!-- ocr:type:dest --> 占位为 OCR 文本或 defer 标记.

    三种并行模式:
    - 预计算 (thread 模式, batch): 命中 _ocr_text_cache, 直接查表 (由
      _precompute_ocr 用全局 ThreadPool 跑完). 无空洞.
    - process: ProcessPool, 各进程独立引擎, 无 session 锁. pool 由 run() 预创建.
    - thread (流式/单文档回退): 本函数内建 ThreadPool.
    """
    adir = out / "original-doc" / f"{doc_id}_assets"
    matches = list(_OCR_RE.finditer(body))
    if not matches:
        return body

    def defer_reason(m, reason=None):
        """构造 defer 标记 (无 OCR 文本时). reason 为空则不带 note.

        cls 从资产字节推断 (与 parser 侧同口径), 让 stage2 免探测即知
        该用何方式处理 (diagram/chart/photo/screenshot).
        """
        ocr_type = m.group(1)
        dtype = "scan-page" if ocr_type == "scan" else "image-ocr"
        cls = ""
        try:
            blob = (adir / m.group(2)).read_bytes()
            cls = guess_image_class(blob, m.group(2))
        except Exception:
            pass
        if reason:
            return defer(deferred_list, dtype, 1, reason, cls=cls)
        return defer(deferred_list, dtype, 1, cls=cls)

    def empty_text_reason(m):
        """OCR 结果为空的真正原因 (就地重判, 避免并行 worker 丢信息).

        worker (process / 预计算) 把"文件丢失""格式不支持""无文字"都压成
        空文本, 无法区分. 此处用 asset 路径重新判定, 与单图直算路径
        (ocr_one) 口径一致, 保证 batch/process/流式三种模式 note 相同.
        """
        asset_path = adir / m.group(2)
        if not asset_path.exists():
            return "资产文件丢失"
        try:
            blob = asset_path.read_bytes()
        except Exception:
            return "资产文件读取失败"
        from . import vectimg
        if vectimg.is_vector_blob(blob):
            if vectimg.render_vector(blob) is None:
                return "矢量图无法渲染(需 Windows/ LibreOffice/Inkscape)"
        return ""  # OCR 已跑但未检出文字

    def make_repl(m, txt):
        if txt:
            return f"```\n{txt}\n```"
        return defer_reason(m, empty_text_reason(m))

    # 预计算命中: 直接查表 (全局池已跑完)
    if _ocr_text_cache and OCR_MODE != "process":
        replacements = []
        for m in matches:
            dest = m.group(2)
            if (doc_id, dest) in _ocr_text_cache:
                replacements.append(make_repl(m, _ocr_text_cache[(doc_id, dest)]))
            else:
                # 缓存缺该项 (图不存在/不支持) -> defer
                replacements.append(make_repl(m, None))
    else:
        def ocr_one(m):
            return make_repl(m, _ocr_asset_text(m, adir))

        # ProcessPool: 多进程并行
        if OCR_MODE == "process" and _ocr_pool is not None and len(matches) >= 2:
            paths = [str(adir / m.group(2)) for m in matches]
            texts = list(_ocr_pool.map(_ocr_proc_worker, paths))
            replacements = [make_repl(m, t) for m, t in zip(matches, texts)]
        # ThreadPool: 单图直接 sub, 多图用 pool
        elif len(matches) == 1:
            return _OCR_RE.sub(ocr_one, body)
        else:
            with ThreadPoolExecutor(max_workers=min(MAX_OCR_THREADS, len(matches))) as pool:
                replacements = list(pool.map(ocr_one, matches))

    parts = []
    last_end = 0
    for m, repl in zip(matches, replacements):
        parts.append(body[last_end:m.start()])
        parts.append(repl)
        last_end = m.end()
    parts.append(body[last_end:])
    return "".join(parts)


def _parse_failure(p, rel, ex):
    """解析失败时构造 defer 占位. 返回 parsed dict."""
    msg = str(ex).lower()
    if "encrypt" in msg or "password" in msg:
        log.error("文档可能加密, 跳过解析 %s: %s", rel, ex)
    else:
        log.error("解析失败 %s: %s", rel, ex)
    deferred_list = []
    return {
        "title": p.stem,
        "body": defer(deferred_list, "parse-failed", 1, f"{ex}; 见 original-doc/{rel}"),
        "tags": [], "meta": {},
        "deferred": deferred_list,
    }


def _child_logging_setup():
    """子进程 logging 配置 (spawn 平台必需).

    Windows 用 spawn 启动子进程: 全新解释器重新 import 模块, 父进程在
    run() 执行的 logging.basicConfig 不会重放, 子进程 root logger 无 handler,
    于是子进程里 parser 的 log.info/warning 被静默丢弃 (Linux fork 继承父进程
    handler, 无此问题). 表现为同一命令 Windows 看不到如 "PDF 检出 N 个跨页样板"
    / "PDF 加密且需口令" 等解析期日志.

    用 if not handlers 守卫: fork 下已继承 handler, 不重复配置 (否则会出现
    重复行). 级别从环境变量读 (父进程按 verbose 写入).
    """
    if logging.getLogger().handlers:
        return
    lvl_name = os.environ.get("DOC2MD_LOG_LEVEL", "INFO")
    level = getattr(logging, lvl_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _parse_worker(handler, path_str, ctx_dict):
    """子进程解析入口: 返回 (parsed, elapsed_seconds). 异常不捕获, 由调用方处理."""
    _child_logging_setup()
    from doc2md import parsers
    t0 = time.time()
    result = parsers.get(handler)(path_str, ctx_dict)
    return result, time.time() - t0


def _reclassify(out, man, conf, rel, old_entry, new_cat):
    """配置变更后重分类: 用已存 title+summary 重判类目, 移动 md, 更新 manifest."""
    doc_id = old_entry["id"]
    old_cat = old_entry.get("category", conf.default_category)
    old_doc_path = old_entry.get("doc_path", "")
    new_doc_path = f"docs/{new_cat}/{doc_id}.md"
    old_md = out / old_doc_path
    new_dir = out / "docs" / new_cat
    new_dir.mkdir(parents=True, exist_ok=True)
    if old_md.exists():
        md_text = old_md.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", md_text, re.S)
        if m:
            fm = yaml.safe_load(m.group(1))
            fm["category"] = new_cat
            new_fm = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
            md_text = f"---\n{new_fm}\n---\n" + md_text[m.end():]
        util.write_text(new_dir / f"{doc_id}.md", md_text)
        old_md.unlink()
    old_entry["category"] = new_cat
    old_entry["doc_path"] = new_doc_path
    log.info("重分类(配置变更): %s %s -> %s", rel, old_cat, new_cat)


def _prune_entry(man, out, rel, entry):
    """清理一个 manifest 条目对应的产物: md / 原文件 / assets 目录 / manifest 条目.

    用于"输入已删除"和"格式变为不支持"两种场景, 避免孤儿/stale.
    """
    did = entry.get("id", "")
    op = out / entry.get("doc_path", "")
    if op.exists() and "docs" in op.parts:
        try:
            op.unlink()
        except Exception as ex:
            log.warning("删孤儿 md 失败 %s: %s", op, ex)
    sp = out / entry.get("source", "")
    if sp.exists() and "original-doc" in sp.parts:
        try:
            sp.unlink()
        except Exception as ex:
            log.warning("删孤儿原文件失败 %s: %s", sp, ex)
    if did:
        adir = out / "original-doc" / f"{did}_assets"
        if adir.exists():
            shutil.rmtree(adir, ignore_errors=True)
    if rel in man.entries:
        del man.entries[rel]


def _table_summary_fallback(body):
    """纯表格/无正文段文档 (如 xlsx) 的占位摘要: 用表格规模描述.

    extract_summary 对无正文段落的文档返回空; 此处给一个可用的描述性摘要,
    便于 catalog 导航. 无表格时返回 ''.
    """
    if "|" not in body:
        return ""
    lines = body.split("\n")
    pipe_lines = [ln for ln in lines if ln.lstrip().startswith("|")]
    if not pipe_lines:
        return ""
    # 分隔行 (|---|...) 与表头行 均非数据行; 逐个表格扣除
    sep_re = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")
    n_sep = sum(1 for ln in pipe_lines if sep_re.match(ln.strip()))
    n_data = max(0, len(pipe_lines) - 2 * n_sep)  # 每表扣 1 分隔 + 1 表头
    return f"（表格文档：约 {n_data} 行数据，详见正文表格）"


def _finalize(out, move, man, conf, p, info, rel, sha, doc_id, parsed, parse_time=0.0):
    """公共收尾(支持格式与 defer 共用): 去噪/分类/重分类清理/拷原文件/side files/写 md/入 manifest.

    返回 "ok" 或 "write_fail"(写 md 失败, 调用方计 errors 且不入 manifest).
    """
    t0 = time.time()
    # md parser 登记的 side files 先拷到 assets (OCR 需读这些文件)
    for src, dest in parsed.get("meta", {}).get("side_files", []):
        adir = out / "original-doc" / f"{doc_id}_assets"
        adir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, str(adir / dest))
        except Exception as ex:
            log.warning("side file 拷贝失败 %s: %s", src, ex)
    body = denoise.clean_text(parsed["body"])
    body = denoise.strip_boilerplate(body)
    body = _post_ocr(body, out, doc_id, parsed.setdefault("deferred", []))
    title = parsed.get("title") or p.stem
    category = conf.classify(p.name, title + "\n" + body[:4000])
    if category not in conf.categories_set():
        category = conf.default_category
    cat_dir = out / "docs" / category
    cat_dir.mkdir(parents=True, exist_ok=True)

    # 分类变了: 删旧类目下的旧 md, 避免孤儿
    new_doc_path = f"docs/{category}/{doc_id}.md"
    old_entry = man.entries.get(rel)
    if old_entry and old_entry.get("doc_path") and old_entry["doc_path"] != new_doc_path:
        op = out / old_entry["doc_path"]
        if op.exists():
            op.unlink()
            log.info("清理旧类目 md: %s -> %s", old_entry["doc_path"], new_doc_path)

    # 拷贝/移动 原文件 (保留相对目录结构)
    dst_orig = out / "original-doc" / rel
    dst_orig.parent.mkdir(parents=True, exist_ok=True)
    try:
        if move:
            shutil.move(str(p), str(dst_orig))
        else:
            shutil.copy2(str(p), str(dst_orig))
    except Exception as ex:
        log.warning("原文件拷贝失败 %s: %s", rel, ex)

    summary = denoise.extract_summary(body)
    if not summary:
        summary = _table_summary_fallback(body)
    sections = denoise.extract_sections(body)
    deferred = aggregate_deferred(parsed.get("deferred", []))
    # 长 md 顶部渲染目录(导航概览, 链接到章节锚点); 短文档不加
    if len(sections) >= 6:
        shown = sections[:30]
        slugs = util.heading_slugs([s["title"] for s in shown])
        toc_lines = "\n".join(f"- [{s['title']}](#{sl})"
                              for s, sl in zip(shown, slugs))
        body = f"## 目录\n\n{toc_lines}\n\n" + body
    doc = {
        "id": doc_id, "title": title, "category": category,
        "source": f"original-doc/{rel}", "source_type": info["format"],
        "tags": parsed.get("tags", []) or [], "summary": summary,
        "deferred": deferred,
    }
    md_text = denoise.front_matter(doc) + body + ("" if body.endswith("\n") else "\n")
    try:
        util.write_text(cat_dir / f"{doc_id}.md", md_text)
    except Exception as ex:
        log.error("写 md 失败 %s: %s", cat_dir / f"{doc_id}.md", ex)
        return "write_fail"
    man.upsert({
        "relpath": rel, "sha256": sha, "id": doc_id, "title": title,
        "category": category, "source": doc["source"], "source_type": doc["source_type"],
        "doc_path": f"docs/{category}/{doc_id}.md", "summary": summary,
        "tags": doc["tags"], "deferred": deferred,
    })
    log.info("处理: %s -> docs/%s/%s.md (%.1fs)", rel, category, doc_id,
             parse_time + (time.time() - t0))
    return "ok"


def _handle_empty(man, out, rel, move):
    """空文件: 跳过并清理旧条目. 返回 (unsupported_delta, purged_delta)."""
    log.warning("跳过空文件: %s", rel)
    purged = 0
    if not move:
        old = man.entries.get(rel)
        if old:
            _prune_entry(man, out, rel, old)
            purged = 1
    return 1, purged


def _try_reclassify(out, man, conf, p, rel):
    """sha 未变时检查配置变更是否需重分类."""
    old_entry = man.entries.get(rel)
    if not old_entry:
        return
    text = old_entry.get("title", "") + "\n" + old_entry.get("summary", "")
    new_cat = conf.classify(p.name, text)
    old_cat = old_entry.get("category", conf.default_category)
    if new_cat != old_cat:
        _reclassify(out, man, conf, rel, old_entry, new_cat)


def _clear_old_assets(out, doc_id):
    """清理该 doc 的旧 assets 目录, 避免旧版图残留."""
    old_assets = out / "original-doc" / f"{doc_id}_assets"
    if old_assets.exists():
        shutil.rmtree(old_assets, ignore_errors=True)


class _RunState:
    """一次 run 的可变状态 (计数器/中断标志/阶段计时/待处理队列).

    把原本散在 run() 里的共享可变变量收进一个对象, 使各阶段能拆成独立函数
    而无需依赖模块级 global. 仅存运行期数据, 不跨 run 复用.
    """

    def __init__(self, in_dir, out, move):
        self.in_dir = in_dir
        self.out = out
        self.move = move
        self.conf = None
        self.man = None
        self.current_rels = set()
        self.parse_items = []      # (p, info, rel, sha, doc_id, ctx, handler)
        self.finalize_items = []   # (p, info, rel, sha, doc_id, parsed, is_error)
        self.processed = 0
        self.skipped = 0
        self.errors = 0
        self.unsupported = 0
        self.purged = 0
        self.deferred = 0
        self.total_ocr_images = 0
        self.interrupted = False
        self.t_scan = 0.0
        self.t_parse = 0.0
        self.t_ocr = 0.0


def _finalize_one(st, p, info, rel, sha, doc_id, parsed, parse_time=0.0):
    """收尾单个文档并累计计数 (统一 processed/errors 口径)."""
    try:
        status = _finalize(st.out, st.move, st.man, st.conf, p, info, rel, sha,
                           doc_id, parsed, parse_time)
        if status == "write_fail":
            st.errors += 1
        else:
            st.processed += 1
    except Exception as ex:
        log.error("收尾失败 %s: %s", rel, ex)
        st.errors += 1


def run(input_dir, output_dir, config_path=None, move=False, verbose=False):
    """主流程入口: 路径校验 -> 三阶段处理 -> 清理 + 索引.

    返回 (processed, skipped, errors). 中断时保存已处理部分.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # 传给 spawn 子进程 (子进程重配 logging 时读; fork 下无害)
    os.environ["DOC2MD_LOG_LEVEL"] = logging.getLevelName(level)
    _t_start = time.time()
    # 压掉 pdfminer/PIL 的内部噪声 (FontBBox 等), 只留 ERROR
    for noisy in ("pdfminer", "pdfminer.six", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    # --- 资源嗅探: 自动调节并行度 (无人工配置) ---
    global MAX_WORKERS, MAX_OCR_THREADS, BATCH_MODE, OCR_MODE, OCR_PROCS, _ocr_pool
    global _ocr_text_cache, _CPUS, _AVAIL_MB
    _ocr_text_cache = {}  # 每轮清空 (防上次残留)
    ocr.reset_ocr_stats()  # 清零累计 OCR 耗时 (本进程)
    if MAX_WORKERS is None or MAX_OCR_THREADS is None or BATCH_MODE is None:
        dw, dt, cpus, avail_mb_auto, batch_mode = _detect_resources()
        _CPUS = cpus
        _AVAIL_MB = avail_mb_auto
        if MAX_WORKERS is None:
            MAX_WORKERS = dw
        if MAX_OCR_THREADS is None:
            MAX_OCR_THREADS = dt
        if BATCH_MODE is None:
            BATCH_MODE = batch_mode
    else:
        # 测试强制了并行参数: 不重新嗅探/设限, 但抓真实核数/内存供日志
        _CPUS = _CPUS or (os.cpu_count() or 1)
        if not _AVAIL_MB:
            _AVAIL_MB = _probe_avail_mb()
    batch_mode = BATCH_MODE  # noqa: F841

    # --- 路径校验 ---
    in_dir = Path(input_dir).resolve()
    out = Path(output_dir).resolve()
    if not in_dir.exists() or not in_dir.is_dir():
        log.error("输入目录不存在或不是目录: %s", input_dir)
        return 0, 0, 1
    if in_dir == out:
        log.error("输入与输出不能是同一目录: %s", in_dir)
        return 0, 0, 1
    try:
        out.relative_to(in_dir)  # 输出在输入内部 -> 递归污染
        log.error("输出目录不能在输入目录内部: %s 在 %s 内", out, in_dir)
        return 0, 0, 1
    except ValueError:
        pass

    conf = load_config(config_path)
    (out / "index" / "process").mkdir(parents=True, exist_ok=True)
    (out / "index" / "artifact").mkdir(parents=True, exist_ok=True)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    (out / "original-doc").mkdir(parents=True, exist_ok=True)
    man = manifest.Manifest(out / "index" / "process" / ".manifest.yaml")

    files = [p for p in in_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
    files.sort(key=lambda p: str(p).lower())
    if not files:
        log.warning("输入目录为空, 无文件可处理: %s", in_dir)

    st = _RunState(in_dir, out, move)
    st.conf = conf
    st.man = man
    st.current_rels = {util.relpath(p, in_dir) for p in files}

    # === 阶段 1: 串行预处理 (检测/sha/跳过/重分类/不支持格式) ===
    _scan_phase(st, files)

    # === 阶段 2+3: 解析+收尾 ===
    # batch (≥4GB): 全部解析 → shutdown pool → OCR. worker 与 OCR 不重叠, CPU 不叠加.
    # streaming (<4GB): 解析一个 → 立即 OCR+写出+gc. 低内存安全, 1 worker 不叠加.
    if not st.interrupted and st.parse_items:
        _parse_finalize_phase(st, batch_mode)

    # shutdown OCR ProcessPool (如有)
    if _ocr_pool is not None:
        _ocr_pool.shutdown(wait=True)
        _ocr_pool = None

    # finalize_items: 无需解析, 直接收尾
    if not st.interrupted:
        _finalize_no_parse_phase(st)

    # === 阶段 4: 清理 + 保存 + 生成索引 ===
    _cleanup_index_phase(st)

    log.info("完成: 处理 %d, 跳过 %d, 失败 %d, 清理 %d, 不支持 %d, defer %d (%.1fs)",
             st.processed, st.skipped, st.errors, st.purged, st.unsupported, st.deferred,
             time.time() - _t_start)
    if not st.interrupted:
        _log_phase_timing(st, time.time() - _t_start)
    return st.processed, st.skipped, st.errors


def _scan_phase(st, files):
    """阶段 1: 逐文件检测/哈希/跳过/重分类, 产出 parse_items 与 finalize_items.

    串行执行 (轻量: 只读文件头做签名识别, 不解析内容).
    """
    in_dir, out, move = st.in_dir, st.out, st.move
    man, conf = st.man, st.conf
    _ts = time.time()
    try:
        for p in files:
            info = detector.detect(str(p))
            handler = info["handler"]
            rel = util.relpath(p, in_dir)
            sha = util.sha256_file(p)
            doc_id = util.stable_doc_id(rel)

            if handler is None or parsers.get(handler) is None:
                if info["format"] == "empty":
                    u, pu = _handle_empty(man, out, rel, move)
                    st.unsupported += u
                    st.purged += pu
                    continue
                if not move and man.is_unchanged(rel, sha):
                    st.skipped += 1
                    continue
                _clear_old_assets(out, doc_id)
                deferred_list = []
                parsed = {
                    "title": p.stem,
                    "body": defer(deferred_list, "unsupported", 1,
                                  f"格式'{info['format']}' stage1未解析, 见 original-doc/{rel}"),
                    "tags": [], "meta": {},
                    "deferred": deferred_list,
                }
                st.deferred += 1
                log.info("defer(stage2): %s (format=%s)", rel, info["format"])
                st.finalize_items.append((p, info, rel, sha, doc_id, parsed, False))
            else:
                if man.is_unchanged(rel, sha):
                    _try_reclassify(out, man, conf, p, rel)
                    st.skipped += 1
                    continue
                ctx = dict(info.get("meta", {}))
                ctx.update({"relpath": rel, "doc_id": doc_id, "output_root": out})
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                if size > LARGE_FILE_MB * 1024 * 1024:
                    log.warning("大文件(%d MB), 解析可能较慢: %s", size // (1024 * 1024), rel)
                _clear_old_assets(out, doc_id)
                st.parse_items.append((p, info, rel, sha, doc_id, ctx, handler))
    except KeyboardInterrupt:
        st.interrupted = True
        log.warning("收到中断信号 (扫描阶段), 保存已处理部分并退出...")

    st.t_scan = time.time() - _ts
    if not st.interrupted:
        log.info("扫描完成: %d 待解析, %d 跳过, %d 不支持 (共 %d 文件)",
                 len(st.parse_items), st.skipped, len(st.finalize_items), len(files))


def _finalize_no_parse_phase(st):
    """finalize_items: 无需解析, 直接收尾."""
    try:
        for item in st.finalize_items:
            p, info, rel, sha, doc_id, parsed, _is_err = item
            _finalize_one(st, p, info, rel, sha, doc_id, parsed)
    except KeyboardInterrupt:
        st.interrupted = True
        log.warning("收到中断信号 (收尾阶段), 保存已处理部分并退出...")


def _cleanup_index_phase(st):
    """阶段 4: 清理已删除输入 + 保存 manifest + 生成索引."""
    if not st.move:
        for rp in list(st.man.entries):
            if rp in st.current_rels:
                continue
            _prune_entry(st.man, st.out, rp, st.man.entries[rp])
            st.purged += 1
            log.info("清理已删除输入: %s", rp)
    try:
        st.man.save()
    except Exception as ex:
        log.error("manifest 保存失败: %s", ex)
    try:
        catalog.build_catalog(st.man.values(), st.out)
        catalog.build_handoff(st.man.values(), st.out)
    except Exception as ex:
        log.error("catalog/handoff 生成失败: %s", ex)


def _log_phase_timing(st, total):
    """日志: 分阶段耗时 (扫描/解析/OCR/写出/索引).

    OCR 耗时: batch 模式用相位墙钟; 流式/内联用累计器的墙钟跳度.
    图数: 多进程时子进程计数不可见, 用本 run 统计的 total_ocr_images.
    流式模式下 OCR 内联在解析阶段, 从解析中扣除, 避免重叠计.
    """
    _ocr_sum, _ocr_n, _ocr_span = ocr.ocr_stats()
    t_ocr_show = max(st.t_ocr, _ocr_span)
    n_show = max(_ocr_n, st.total_ocr_images)
    t_parse_show = max(0.0, st.t_parse - (t_ocr_show - st.t_ocr))
    t_other = max(0.0, total - st.t_scan - t_parse_show - t_ocr_show)
    log.info("阶段耗时: 扫描 %.1fs | 解析 %.1fs | OCR %.1fs (%d 图) | 写出/索引 %.1fs | 总计 %.1fs",
             st.t_scan, t_parse_show, t_ocr_show, n_show, t_other, total)


def _parse_finalize_phase(st, batch_mode):
    """阶段 2+3: 并行解析 + 收尾 (含 OCR 预计算/写出).

    batch (≥4GB): 全部解析 → shutdown pool → OCR. worker 与 OCR 不重叠, CPU 不叠加.
    streaming (<4GB): 解析一个 → 立即 OCR+写出+gc. 低内存安全, 1 worker 不叠加.
    """
    global OCR_MODE, OCR_PROCS, _ocr_pool
    in_dir, out, move = st.in_dir, st.out, st.move
    man, conf = st.man, st.conf
    parse_items = st.parse_items
    max_workers = min(MAX_WORKERS, len(parse_items))
    log.info("解析 %d 个文件 (%d 进程, %s)...", len(parse_items), max_workers,
             "批量" if batch_mode else "流式")
    pool = ProcessPoolExecutor(max_workers=max_workers)
    future_map = {}
    for item in parse_items:
        p, info, rel, sha, doc_id, ctx, handler = item
        future_map[rel] = pool.submit(_parse_worker, handler, str(p), ctx)
    ocr_announced = False
    parse_results = {}  # batch 模式: 暂存解析结果

    _ts = time.time()
    try:
        for item in parse_items:
            p, info, rel, sha, doc_id, ctx, handler = item
            if st.interrupted:
                break
            try:
                parsed, parse_time = future_map[rel].result()
                log.info("解析完成: %s (%.1fs)", rel, parse_time)
            except KeyboardInterrupt:
                st.interrupted = True
                break
            except Exception as ex:
                parsed = _parse_failure(p, rel, ex)
                st.errors += 1
                parse_time = 0.0
                log.info("解析失败: %s", rel)
                st.deferred += 1

            if batch_mode:
                parse_results[rel] = (parsed, parse_time)
            else:
                n_ocr = len(_OCR_RE.findall(parsed.get("body", "")))
                if n_ocr > 0 and not ocr_announced:
                    log.info("OCR 模式: 流式 → ThreadPool %d 线程 (低内存, 图数未知)",
                             MAX_OCR_THREADS)
                    ocr_announced = True
                if n_ocr > 0:
                    log.info("OCR: %s, %d 张图片", rel, n_ocr)
                _finalize_one(st, p, info, rel, sha, doc_id, parsed, parse_time)
                del parsed
                gc.collect()
    except KeyboardInterrupt:
        st.interrupted = True
    finally:
        for f in future_map.values():
            f.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
    st.t_parse = time.time() - _ts  # 解析阶段 (仅本次并行解析)
    ocr_t0 = time.time()

    # batch 模式: pool 已 shutdown (worker 进程已退出), OCR 不与解析叠加
    # 即使中断, 仍写出已解析完的文件 (parse_results 只含成功的)
    if batch_mode:
        # 计数总 OCR 图数, 决策 OCR 并行模式
        st.total_ocr_images = sum(
            len(_OCR_RE.findall(pr[0].get("body", "")))
            for pr in parse_results.values())
        if OCR_MODE is None or OCR_PROCS is None:
            m, w = _ocr_mode_decision(batch_mode, MAX_OCR_THREADS, _AVAIL_MB, st.total_ocr_images)
            if OCR_MODE is None:
                OCR_MODE = m
            if OCR_PROCS is None:
                OCR_PROCS = w
        _log_ocr_decision(batch_mode, st.total_ocr_images)
        # ProcessPool: 预创建, 计时
        if OCR_MODE == "process" and _ocr_pool is None:
            t_pool = time.time()
            _ocr_pool = ProcessPoolExecutor(
                max_workers=OCR_PROCS, initializer=_ocr_proc_init)
            log.info("OCR ProcessPool 就绪 (%d worker, %.1fs)",
                     OCR_PROCS, time.time() - t_pool)
        # 全局预计算 OCR: 一个池跑完所有文档的所有图片 (消除逐文档并行度空洞)
        if OCR_MODE == "thread" and st.total_ocr_images > 0:
            jobs = []
            for item in parse_items:
                p2, info2, rel2, sha2, doc_id2, ctx2, handler2 = item
                if rel2 not in parse_results:
                    continue
                parsed2 = parse_results[rel2][0]
                for dest in _OCR_RE.findall(parsed2.get("body", "")):
                    ap = out / "original-doc" / f"{doc_id2}_assets" / dest[1]
                    jobs.append((doc_id2, dest[1], str(ap)))
            ocr_t0 = time.time()
            try:
                _precompute_ocr(jobs, out)
            except KeyboardInterrupt:
                st.interrupted = True
                log.warning("收到中断信号 (OCR 预计算阶段), 保存已处理部分并退出...")
        try:
            for item in parse_items:
                p, info, rel, sha, doc_id, ctx, handler = item
                if rel not in parse_results:
                    continue
                parsed, parse_time = parse_results.pop(rel)
                _finalize_one(st, p, info, rel, sha, doc_id, parsed, parse_time)
                del parsed
                gc.collect()
        except KeyboardInterrupt:
            st.interrupted = True
            log.warning("收到中断信号 (收尾阶段), 保存已处理部分并退出...")

        # OCR 计时汇总
        if st.total_ocr_images > 0:
            ocr_elapsed = time.time() - ocr_t0
            log.info("OCR 完成: %d 图, %.1fs, %.1f 图/s",
                     st.total_ocr_images, ocr_elapsed,
                     st.total_ocr_images / ocr_elapsed if ocr_elapsed > 0 else 0)
        st.t_ocr = time.time() - ocr_t0

    if st.interrupted:
        log.warning("收到中断信号 (解析/收尾阶段), 保存已处理部分并退出...")


def _log_ocr_decision(batch_mode, total_ocr_images):
    """日志: OCR 并行决策详情 (一行含模式/数量/判定依据)."""
    if total_ocr_images <= 0:
        return
    proc_budget = max(0, (_AVAIL_MB - 1024) // 400)
    need_by_imgs = total_ocr_images // 10
    # 自动决策下的“应然”模式 (用于解释; 即使被测试强制也说得清)
    auto_mode, auto_w = _ocr_mode_decision(
        batch_mode, MAX_OCR_THREADS, _AVAIL_MB, total_ocr_images)
    forced = " (测试强制)" if (OCR_MODE != auto_mode) else ""
    if OCR_MODE == "process":
        log.info(
            "OCR 决策: process(%d 进程)%s [Linux+batch+图%d≥50+核%d≥2, "
            "worker=min(OCR%d, 图/10=%d, 内存%d) → %d]",
            OCR_PROCS, forced, total_ocr_images, _CPUS,
            MAX_OCR_THREADS, need_by_imgs, proc_budget, OCR_PROCS)
    else:
        if sys.platform == "win32":
            reason = "Windows:spawn开销高"
        elif total_ocr_images < 50:
            reason = "图%d<50" % total_ocr_images
        elif not batch_mode:
            reason = "非batch(可用%.1fGB<4GB)" % (_AVAIL_MB / 1024)
        elif MAX_OCR_THREADS < 2:
            reason = "max_ocr<2"
        elif proc_budget < 2:
            reason = "内存不足(可容%d进程<2)" % proc_budget
        else:
            reason = "自动应选process"
        log.info(
            "OCR 决策: thread(%d 线程)%s [%s; 核%d, 可用%.1fGB, 图%d]",
            MAX_OCR_THREADS, forced, reason, _CPUS, _AVAIL_MB / 1024,
            total_ocr_images)
    ocr_unit = "进程" if OCR_MODE == "process" else "线程"
    ocr_n = OCR_PROCS if OCR_MODE == "process" else MAX_OCR_THREADS
    log.info("OCR: %d 张图片 (%d %s, %s)...",
             total_ocr_images, ocr_n, ocr_unit, OCR_MODE)
