"""Windows 低成本验证: rec_batch_num / cls_batch_num 调大的收益.

背景: AI 建议调大 RapidOCR 的 Rec.rec_batch_num (默认 6) 与 Cls.cls_batch_num,
      验证批处理收益. 我们已知 Python 胶水仅占 0.1% (非 GIL 瓶颈), 但批大小
      可能影响 rec 段效率, 值得低成本实测.

方法: 同一批真实图, 用不同 batch_num 跑完整 OCR 流水线, 记录总耗时 +
      各段 (det/cls/rec) 耗时. 单线程串行 (隔离批处理本身的效果, 不受并发干扰).

用法:
  python tests/diag_win_batchnum.py C:\\Users\\...\\knowledge\\original-doc
  环境变量 DIAG_STRIDE=3 抽样 (默认 3), DIAG_BATCHES=6,16,32,64 指定批大小.
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
    print("Windows 低成本验证: rec_batch_num / cls_batch_num 调大")
    print("=" * 72)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    import glob as _glob

    real_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
    expanded = []
    for d in real_dirs:
        subs = _glob.glob(os.path.join(d, "*_assets"))
        expanded.extend(subs or [d])
    blobs = load_blobs(expanded) if expanded else []
    if not blobs:
        print("!! 未找到图片, 请传 original-doc 目录")
        return
    stride = int(os.environ.get("DIAG_STRIDE", "3"))
    if stride > 1:
        blobs = blobs[::stride]
    batches = [int(x) for x in os.environ.get("DIAG_BATCHES", "6,16,32,64").split(",") if x.strip()]
    print("图片: %d 张; 批大小档: %s" % (len(blobs), batches))

    from rapidocr_onnxruntime import RapidOCR

    print("-" * 72)
    print("单线程串行对比 (隔离批处理效果):")
    hdr = "  batch=%-4d  total=%6.1fs  %5.3fs/图  det=%5.1fs(%4.0f%%)  rec=%5.1fs(%4.0f%%)  cls=%4.1fs"
    baseline = None
    results = []
    for bn in batches:
        eng = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1,
                       rec_batch_num=bn, cls_batch_num=bn)
        acc = {"det": 0.0, "cls": 0.0, "rec": 0.0}
        lock = __import__("threading").Lock()

        def wrap(name, attr):
            orig = getattr(eng, attr)

            def fn(*a, **k):
                t0 = time.perf_counter()
                r = orig(*a, **k)
                dt = time.perf_counter() - t0
                with lock:
                    acc[name] += dt
                return r
            setattr(eng, attr, fn)

        wrap("det", "auto_text_det")
        wrap("cls", "text_cls")
        wrap("rec", "text_rec")

        eng(blobs[0])  # 预热
        for k in acc:
            acc[k] = 0.0
        t = time.time()
        for b in blobs:
            try:
                eng(b)
            except Exception as e:
                print("  batch=%d 单图失败: %s" % (bn, e))
        total = time.time() - t
        if baseline is None:
            baseline = total
        n = len(blobs)
        print(hdr % (bn, total, total / n,
                     acc["det"], acc["det"] / total * 100,
                     acc["rec"], acc["rec"] / total * 100,
                     acc["cls"]))
        results.append((bn, total, acc))
        del eng

    print("-" * 72)
    print("汇总 (相对 baseline batch=%d):" % batches[0])
    for bn, total, acc in results:
        print("  batch=%-4d  total=%6.1fs  speedup=%.2fx  (rec=%.1fs %+.0f%%)" %
              (bn, total, baseline / total, acc["rec"],
               (acc["rec"] / results[0][2]["rec"] - 1) * 100 if results[0][2]["rec"] else 0))
    print("=" * 72)
    print("判读: 若调大批后 total 明显下降 => 批处理有效, 值得继续.")
    print("      若无改善甚至变慢 => 否决此路线, 瓶颈不在这里 (已知短板是 det).")


if __name__ == "__main__":
    main()
