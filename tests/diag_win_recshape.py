"""Windows 诊断 v7: rec 动态形状是否是并发天花板.

背景 (diag v6, Windows 16核有效, 52真实图):
  rec 占单图 70%, det 29%, 胶水 0.1% (GIL 非瓶颈).
  det 与 rec 并发都卡 ~2.6x, 但 diag B 纯 rec session 合成固定输入达 7.3x.
  => 怀疑真实 rec 输入"形状每批不同"(max_wh_ratio 决定宽度)导致 ONNX
     反复重分配/换核, 并发上不去.

方法: 直接调 rec session (text_recognize.session), 两种输入:
  FIX: 固定形状 (1,3,48,320) 反复喂 -> 若扩展好(>5x) => 形状是元凶
  VAR: 真实多样形状 (从真实图 det 出的框, 宽度各异) -> 若扩展差 => 坐实

用法: python tests/diag_win_recshape.py <orig-doc目录>
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


def curve(fn, items, counts, label, unit="run"):
    from concurrent.futures import ThreadPoolExecutor
    print("-" * 70)
    print(label)
    base = None
    for nw in counts:
        t = time.time()
        with ThreadPoolExecutor(nw) as p:
            list(p.map(fn, items))
        dt = time.time() - t
        if base is None:
            base = dt
        print("  threads=%-3d  %6.2fs  %.1fms/%s  speedup=%.2fx" %
              (nw, dt, dt / len(items) * 1000, unit, base / dt))


def main():
    print("=" * 70)
    print("Windows 诊断 v7: rec 动态形状 vs 固定形状 的并发扩展")
    print("=" * 70)
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
        subs = _glob.glob(os.path.join(d, "*_assets"))
        expanded.extend(subs or [d])
    blobs = load_blobs(expanded) if expanded else []
    if not blobs:
        print("!! 未找到图片, 请传 original-doc 目录")
        return
    stride = int(os.environ.get("DIAG_STRIDE", "4"))
    if stride > 1:
        blobs = blobs[::stride]
    print("图片: %d 张" % len(blobs))

    eng = ocr._engine_obj()
    sess = eng.text_rec.session   # OrtInferSession
    try:
        iname = sess.get_input_names()
    except Exception:
        iname = None
    print("rec session:", type(sess).__name__, "input:", iname)
    counts = [1, 4, 8, 12]

    # --- 造真实 rec 输入: 对每张图跑 det 得到框, 裁出 rec 输入张量 ---
    def get_rec_inputs(img):
        """返回该图的 rec 批次张量列表 (真实形状)."""
        im = eng.load_img(img)
        im, _, _ = eng.preprocess(im)
        im, _ = eng.maybe_add_letterbox(im, {})
        boxes, _ = eng.auto_text_det(im)
        if boxes is None or len(boxes) == 0:
            return []
        crops = eng.get_crop_img_list(im, boxes)
        # 复刻 rec 的 resize: 用这批最大 wh_ratio
        batches = []
        bn = eng.text_rec.rec_batch_num
        for beg in range(0, len(crops), bn):
            end = min(len(crops), beg + bn)
            max_wh = eng.text_rec.rec_image_shape[2] / eng.text_rec.rec_image_shape[1]
            for i in range(beg, end):
                h, w = crops[i].shape[:2]
                max_wh = max(max_wh, w / h)
            nb = []
            for i in range(beg, end):
                norm = eng.text_rec.resize_norm_img(crops[i], max_wh)
                nb.append(norm[np.newaxis, :])
            arr = np.concatenate(nb).astype(np.float32)
            batches.append(arr)
        return batches

    # 收集真实 rec 输入 (多样形状)
    var_inputs = []
    shapes = []
    for b in blobs:
        try:
            for arr in get_rec_inputs(b):
                var_inputs.append(arr)
                shapes.append(arr.shape)
        except Exception:
            pass
    if not var_inputs:
        print("!! 未取得 rec 输入")
        return
    uniq = sorted(set(shapes))
    print("真实 rec 批次: %d 个, 形状种类 %d %s..." %
          (len(var_inputs), len(uniq), uniq[:5]))

    # 固定形状张量 (取真实里最常见的形状固定)
    common = max(set(shapes), key=shapes.count)
    fixed = np.zeros(common, np.float32)
    fixed_items = [fixed for _ in var_inputs]

    # --- 直接喂 session ---
    def run_var(a):
        return sess(a)

    def run_fix(a):
        return sess(a)

    sess(fixed)  # 预热
    curve(run_fix, fixed_items, counts, "FIX: 固定形状 %s (重复)" % (common,))
    curve(run_var, var_inputs, counts, "VAR: 真实多样形状 (%d 种)" % len(uniq))

    print("=" * 70)
    print("判读: FIX 明显好于 VAR (如 >5x vs ~2.6x) => 动态形状是天花板,")
    print("      可通过'按形状分桶/补零固定尺寸'优化.")
    print("      若 FIX 也 ~2.6x => 硬件上限, OCR 并行到此为止.")
    print("=" * 70)


if __name__ == "__main__":
    main()
