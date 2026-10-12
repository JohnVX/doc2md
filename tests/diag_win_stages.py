"""Windows 诊断 v6: 单图 OCR 耗时拆解 (det / cls / rec) + 各段并发扩展.

目的: 定位'12线程只跑出 2.6x'的天花板根因.
  - 纯 ONNX session 16线程 7.3x, 但完整引擎 12线程仅 2.6x.
  - 猜测: det(整图重卷积) 是每图必经的串行段; 或 Python 胶水持 GIL.

方法: monkey-patch 引擎实例的 auto_text_det / text_cls / text_rec, 分别计时.
输出:
  1. 单图各段平均耗时 + 占比 (串行 baseline)
  2. 各段在 1/4/8/12 线程下的扩展比 -> 谁不扩展谁是瓶颈

用法 (真实图, 快):
  python tests/diag_win_stages.py C:\\Users\\...\\knowledge\\original-doc
  环境变量 DIAG_STRIDE=N 抽样, DIAG_THREADS=1,4,8,12 指定档位.
"""
import os
import sys
import time
import threading

os.environ.setdefault("OMP_NUM_THREADS", "1")


def load_blobs(dirs):
    import glob
    blobs = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*"))):
            try:
                b = open(f, "rb").read()
            except Exception:
                continue
            if len(b) < 4:
                continue
            if b[:4] == b"\xd7\xcd\xc6\x9a":
                continue
            if b[:4] == b"\x01\x00\x00\x00" and len(b) > 44 and b[40:44] == b" EMF":
                continue
            blobs.append(b)
    return blobs


def main():
    print("=" * 70)
    print("Windows 诊断 v6: OCR 单图耗时拆解 (det/cls/rec) + 并发扩展")
    print("=" * 70)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr

    import glob as _glob
    real_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
    expanded = []
    for d in real_dirs:
        subs = _glob.glob(os.path.join(d, "*_assets"))
        expanded.extend(subs or [d])
    real_dirs = expanded
    blobs = load_blobs(real_dirs) if real_dirs else []
    if not blobs:
        print("!! 未找到真实图片, 请传入资产目录")
        return
    stride = int(os.environ.get("DIAG_STRIDE", "3"))
    if stride > 1:
        blobs = blobs[::stride]
    thr_env = os.environ.get("DIAG_THREADS", "1,4,8,12")
    thread_counts = [int(x) for x in thr_env.split(",") if x.strip()]
    print("数据源: 真实图片 %d 张" % len(blobs))

    eng = ocr._engine_obj()
    try:
        so = eng.text_det.infer.session.get_session_options()
        print("DET intra_op =", so.intra_op_num_threads)
    except Exception:
        pass

    # --- 逐段计时 (monkey-patch 实例方法) ---
    stage_times = {"det": [], "cls": [], "rec": [], "crop": [], "pre": []}
    tlock = threading.Lock()
    _t = {}

    def wrap(name, attr):
        orig = getattr(eng, attr)

        def fn(*a, **k):
            t0 = time.perf_counter()
            r = orig(*a, **k)
            dt = time.perf_counter() - t0
            with tlock:
                stage_times[name].append(dt)
            return r
        setattr(eng, attr, fn)

    wrap("det", "auto_text_det")
    wrap("cls", "text_cls")
    wrap("rec", "text_rec")
    wrap("crop", "get_crop_img_list")
    wrap("pre", "preprocess")

    # 先单线程跑一遍全量, 得到各段占比
    def full(b):
        return eng(b)

    eng(blobs[0])  # 预热
    for k in stage_times:
        stage_times[k].clear()
    t = time.time()
    for b in blobs:
        eng(b)
    single_total = time.time() - t
    n = len(blobs)
    print("-" * 70)
    print("单线程各段耗时 (共 %d 图, 总 %.1fs, %.3fs/图):" % (n, single_total, single_total / n))
    tot_stage = 0.0
    for name in ["det", "rec", "cls", "crop", "pre"]:
        ts = stage_times[name]
        if not ts:
            continue
        s = sum(ts)
        tot_stage += s
        print("  %-6s %7.1fs  %.3fs/图  %5.1f%%  (调用 %d 次)" %
              (name, s, s / n, s / single_total * 100, len(ts)))
    other = single_total - tot_stage
    print("  %-6s %7.1fs  %5.1f%%  (引擎内部其它/胶水)" %
          ("其它", other, other / single_total * 100))

    # --- 各段并发扩展 (分别隔离测) ---
    print("-" * 70)
    print("各段并发扩展 (各段独立跑, 看哪段不扩展):")
    import numpy as np
    # 准备: 单图 det 输入
    from concurrent.futures import ThreadPoolExecutor

    # det 输入: 预处理后的图
    arr = eng.load_img(blobs[0])
    arr, _, _ = eng.preprocess(arr)
    arr, _ = eng.maybe_add_letterbox(arr, {})

    for nw in thread_counts:
        # det-only
        t = time.time()
        with ThreadPoolExecutor(nw) as p:
            list(p.map(lambda _: eng.auto_text_det(arr), range(len(blobs))))
        det_t = time.time() - t
        print("  threads=%-3d  det-only(同图) %6.2fs  %.3fs/次" %
              (nw, det_t, det_t / len(blobs)))

    print("=" * 70)
    print("判读: 若 det 占比最高且 det-only 扩展差 => det 是串行瓶颈.")
    print("      若 '其它/胶水' 占比高 => Python 胶水持 GIL 是瓶颈.")
    print("=" * 70)


if __name__ == "__main__":
    main()
