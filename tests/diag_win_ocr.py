"""Windows OCR 并行诊断 (独立脚本, 不改动 doc2md).

在 Windows 上运行:  python tests/diag_win_ocr.py
它会输出一堆数字, 把完整输出贴回来即可.

目的: 定位"28 核却只 1.25x 加速"的真实瓶颈. 逐个隔离:
  A. 环境信息 (核数/可用核/ONNX 版本/providers)
  B. 纯 ONNX rec session: 共享 session 多线程 Run 的扩展曲线 (隔离 GIL/session 锁)
  C. 完整 rapidocr eng() 多线程扩展曲线 (真实流水线)
  D. intra_op 线程数的影响
  E. 单张 vs 多张耗时 (看检测占比)
  F. affinity 限制前后对比

无需任何参数, 直接跑. 需要 rapidocr-onnxruntime + onnxruntime + opencv + numpy.
"""
import os, sys, time, threading

os.environ.setdefault("OMP_NUM_THREADS", "1")


def env_info():
    import platform
    print("=" * 70)
    print("A. 环境")
    print("=" * 70)
    print("platform:", platform.platform())
    print("python:", sys.version.split()[0])
    print("cpu_count (os):", os.cpu_count())
    try:
        print("affinity:", len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else "n/a")
    except Exception as e:
        print("affinity err:", e)
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)
    print("providers:", ort.get_available_providers())
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa
        import rapidocr_onnxruntime as r
        print("rapidocr:", getattr(r, "__version__", "?"))
    except Exception as e:
        print("rapidocr import FAIL:", e)
        sys.exit(1)


def make_images(n=12, h=800, w=1100):
    import numpy as np, cv2
    imgs = []
    for k in range(n):
        img = np.full((h, w, 3), 255, np.uint8)
        for i in range(12):
            cv2.putText(img, "Document %d line %d ABCDEF" % (k, i),
                        (40, 50 + i * 60), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (0, 0, 0), 2)
        imgs.append(img)
    return imgs


def curve(fn, imgs, thread_counts, label):
    from concurrent.futures import ThreadPoolExecutor
    print("-" * 70)
    print("曲线:", label, " (图数=%d)" % len(imgs))
    base = None
    for nw in thread_counts:
        t = time.time()
        with ThreadPoolExecutor(max_workers=nw) as p:
            list(p.map(fn, imgs))
        dt = time.time() - t
        if base is None:
            base = dt
        print("  threads=%-3d  %.2fs  %.3fs/img  speedup=%.2fx" %
              (nw, dt, dt / len(imgs), base / dt))


def b_onnx_session():
    print("=" * 70)
    print("B. 纯 ONNX rec session 多线程扩展 (隔离 GIL/流水线)")
    print("=" * 70)
    import numpy as np, onnxruntime as ort
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    # 定位 rec 模型
    try:
        import rapidocr_onnxruntime as r
        mp = Path(r.__file__).parent / "models" / "ch_PP-OCRv4_rec_infer.onnx"
    except Exception:
        mp = None
    if not mp or not mp.exists():
        print("找不到 rec 模型, 跳过 B")
        return
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(mp), sess_options=so,
                                providers=["CPUExecutionProvider"])
    x = sess.get_inputs()[0].name
    inp = np.random.rand(1, 3, 48, 320).astype(np.float32)
    sess.run(None, {x: inp})
    N = 40
    data = [inp] * N
    def run_one(_):
        sess.run(None, {x: inp})
    for nw in (1, 2, 4, 8, 16):
        if nw > os.cpu_count():
            break
        t = time.time()
        with ThreadPoolExecutor(nw) as p:
            list(p.map(run_one, data))
        dt = time.time() - t
        print("  共享session thr=%-3d  %d runs %.2fs  %.1fms/run" %
              (nw, N, dt, dt / N * 1000))


def setup_patched(intra):
    """patch onnxruntime.InferenceSession 注入 intra_op 线程数, 再 import rapidocr."""
    import onnxruntime as ort
    if getattr(ort.InferenceSession, "_diag_patched", False):
        return
    orig = ort.InferenceSession
    class P(orig):
        _diag_patched = True
        def __init__(self, *a, **k):
            if "sess_options" not in k:
                so = ort.SessionOptions()
                so.intra_op_num_threads = int(os.environ.get("DIAG_INTRA", "1"))
                so.inter_op_num_threads = 1
                k["sess_options"] = so
            super().__init__(*a, **k)
    ort.InferenceSession = P


def c_full_pipeline(imgs):
    print("=" * 70)
    print("C. 完整 rapidocr eng() 多线程扩展 (真实流水线, intra_op=1)")
    print("=" * 70)
    os.environ["DIAG_INTRA"] = "1"
    setup_patched(1)
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    eng(imgs[0])
    curve(eng, imgs, [1, 2, 4, 8], "eng()")


def e_single_vs_multi(imgs):
    print("=" * 70)
    print("E. 单张 vs 多张 (看检测/识别占比, 图太小时并行收益低)")
    print("=" * 70)
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    eng(imgs[0])
    t = time.time()
    for im in imgs[:6]:
        eng(im)
    print("  串行 6 张: %.3fs  %.3fs/img" % (time.time() - t, (time.time() - t) / 6))


def f_affinity(imgs):
    print("=" * 70)
    print("F. 当前进程 affinity (模拟 doc2md 的 60% 限制)")
    print("=" * 70)
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        pa = ctypes.c_size_t()
        sa = ctypes.c_size_t()
        k32.GetProcessAffinityMask(k32.GetCurrentProcess(),
                                   ctypes.byref(pa), ctypes.byref(sa))
        print("  process affinity mask: 0x%x (%d cores)" %
              (pa.value, bin(pa.value).count("1")))
    total = os.cpu_count()
    budget = max(2, int(total * 0.6))
    print("  doc2md 会限制到 %d 核 (60%% of %d)" % (budget, total))
    # 限核后再测 eng() 扩展, 看 affinity 是否是真凶
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        mask = (1 << budget) - 1
        ok = k32.SetProcessAffinityMask(k32.GetCurrentProcess(), mask)
        print("  已 SetProcessAffinityMask(0x%x) -> %s (%d 核)" %
              (mask, "OK" if ok else "FAIL", bin(mask).count("1")))
    elif hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, set(range(budget)))
            print("  已 sched_setaffinity -> %d 核" % len(os.sched_getaffinity(0)))
        except Exception as e:
            print("  sched_setaffinity 失败:", e)


def main():
    env_info()
    imgs = make_images(12)
    b_onnx_session()
    e_single_vs_multi(imgs)
    c_full_pipeline(imgs)
    f_affinity(imgs)
    c_full_pipeline(imgs)  # 限核后再跑一次, 对比
    print("=" * 70)
    print("完成. 请把以上完整输出贴回.")
    print("=" * 70)


if __name__ == "__main__":
    main()
