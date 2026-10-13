"""Windows det 专项诊断: 摸清检测阶段瓶颈, 评估能否加速.

背景: v6 显示 det-only 并发仅 ~2.6x (4线程到顶), 疑似内存带宽; 但需排除
      "同图重复"实验偏差, 并拆解 det 内部三段 (preprocess/infer/postprocess),
      看瓶颈是卷积本身还是 Python 后处理 (后者持 GIL).

本脚本只读诊断, 不改生产代码. 采样 + 分档, 控制时长.
测:
  1. det 单次耗时 vs 输入面积 (分档) —— 是否面积主导
  2. det 内部三段拆解 —— infer(卷积) vs postprocess(Python/GIL)
  3. det 并发扩展 (真实多样尺寸)
  4. limit_side_len 降档 (736/512/384) 的耗时 + 检出框数 (精度代价)

用法: python tests/diag_win_det.py <original-doc目录>
  环境变量 DIAG_STRIDE=6 抽样 (默认6).
"""
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")


def load_blobs(dirs):
    import glob
    out = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*"))):
            try:
                b = open(f, "rb").read()
            except Exception:
                continue
            if len(b) >= 4 and b[:4] != b"\xd7\xcd\xc6\x9a":
                out.append(b)
    return out


def main():
    print("=" * 72)
    print("Windows det 专项诊断")
    print("=" * 72)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr
    import glob as _glob
    import numpy as np

    real_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
    expanded = []
    for d in real_dirs:
        expanded.extend(_glob.glob(os.path.join(d, "*_assets")) or [d])
    blobs = load_blobs(expanded) if expanded else []
    if not blobs:
        print("!! 未找到图片, 请传 original-doc 目录")
        return
    stride = int(os.environ.get("DIAG_STRIDE", "6"))
    if stride > 1:
        blobs = blobs[::stride]
    print("图片: %d 张" % len(blobs))

    eng = ocr._engine_obj()
    det = eng.text_det
    try:
        so = det.infer.session.get_session_options()
        print("DET intra_op =", so.intra_op_num_threads)
    except Exception:
        pass

    # --- 准备: 每张图 det 的预处理输入 (形状各异) ---
    def prep(img):
        im = eng.load_img(img)
        im, _, _ = eng.preprocess(im)
        im, _ = eng.maybe_add_letterbox(im, {})
        return im

    items = []
    for b in blobs:
        try:
            im = prep(b)
            det.preprocess_op = det.get_preprocess(max(im.shape[0], im.shape[1]))
            pre = det.preprocess_op(im)
            if pre is None:
                continue
            ori = (im.shape[0], im.shape[1])
            items.append((pre, ori))
        except Exception:
            continue
    print("有效 det 输入: %d" % len(items))

    # === 1. det 单次耗时 vs 输入面积 (分档) ===
    print("-" * 72)
    print("1) det 单次耗时 vs 输入面积:")
    recs = []
    for pre, ori in items:
        t0 = time.perf_counter()
        preds = det.infer(pre)[0]
        t_infer = time.perf_counter() - t0
        t0 = time.perf_counter()
        det.postprocess_op(preds, ori)
        t_post = time.perf_counter() - t0
        recs.append((pre.shape[2] * pre.shape[3], t_infer, t_post))
    recs.sort()
    n = len(recs)
    for label, lo, hi in [("小", 0, n // 3), ("中", n // 3, 2 * n // 3), ("大", 2 * n // 3, n)]:
        seg = recs[lo:hi]
        if not seg:
            continue
        area = sum(r[0] for r in seg) / len(seg)
        ti = sum(r[1] for r in seg) / len(seg)
        tp = sum(r[2] for r in seg) / len(seg)
        print("  %s图: 平均面积 %.2f M像素  infer=%.3fs  post=%.3fs  (post占比 %.0f%%)" %
              (label, area / 1e6, ti, tp, tp / (ti + tp) * 100 if ti + tp else 0))

    # === 2. 三段拆解 (全量均值) ===
    print("-" * 72)
    print("2) det 内部三段拆解 (均值):")
    t_inf = t_pst = 0.0
    for pre, ori in items:
        t0 = time.perf_counter()
        preds = det.infer(pre)[0]
        t_inf += time.perf_counter() - t0
        t0 = time.perf_counter()
        det.postprocess_op(preds, ori)
        t_pst += time.perf_counter() - t0
    tot = t_inf + t_pst
    print("  infer(卷积+ORT胶水) = %.3fs/图 (%.0f%%)" % (t_inf / n, t_inf / tot * 100))
    print("  postprocess(Python)  = %.4fs/图 (%.1f%%)" % (t_pst / n, t_pst / tot * 100))
    print("  => 若 post 占比高 => Python 后处理(可能持GIL)是瓶颈; 若 infer 占绝大多数 => 卷积/带宽瓶颈")

    # === 3. det 并发扩展 (真实多样尺寸) ===
    print("-" * 72)
    print("3) det 并发扩展 (真实多样尺寸, 12线程):")
    from concurrent.futures import ThreadPoolExecutor

    def run_det(idx):
        pre, ori = items[idx % len(items)]
        return det.infer(pre)

    work = list(range(len(items) * 2))  # 跑两遍增加样本
    base = None
    for nw in [1, 4, 8, 12, 16]:
        t = time.time()
        with ThreadPoolExecutor(nw) as p:
            list(p.map(run_det, work))
        dt = time.time() - t
        if base is None:
            base = dt
        print("  threads=%-3d  %6.2fs  %.1fms/次  speedup=%.2fx" %
              (nw, dt, dt / len(work) * 1000, base / dt))

    # === 4. limit_side_len 降档: 耗时 + 检出框数 ===
    print("-" * 72)
    print("4) limit_side_len 降档 (耗时 + 检出框数):")
    sample = blobs[:min(12, len(blobs))]
    print("  (样本 %d 张, 看耗时 vs 原档 736 的比例, 以及框数变化)" % len(sample))
    orig_det = eng.text_det
    for ls in [736, 640, 512, 384]:
        saved = orig_det.limit_side_len
        orig_det.limit_side_len = ls
        t = time.time()
        boxes_total = 0
        for b in sample:
            try:
                im = prep(b)
                boxes, _ = orig_det(im)
                if boxes is not None:
                    boxes_total += len(boxes)
            except Exception:
                pass
        dt = time.time() - t
        orig_det.limit_side_len = saved
        print("  limit_side_len=%-4d  det总=%6.2fs  平均框数=%.1f" %
              (ls, dt, boxes_total / len(sample)))
    print("=" * 72)
    print("判读:")
    print("  - 若 infer 占绝大多数 => 卷积/内存带宽瓶颈, 降分辨率(limit_side_len)是主手段")
    print("  - 若 limit_side_len 降档后框数骤减 => 精度代价高, 不宜降")
    print("  - 若并发在 4 线程到顶 => 带宽饱和, 加线程无用")
    print("=" * 72)


if __name__ == "__main__":
    main()
