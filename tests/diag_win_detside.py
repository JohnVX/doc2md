"""Windows det 精测: limit_side_len 降档的耗时 vs 精度代价.

前次粗测 (12 张) 数据抖动大 (640 档反而慢于 736), 不可信.
本脚本改进:
  - 每档跑 N 遍取最快 (排除冷启动/抖动)
  - 记录总框数 (精度代价)
  - 同一批图逐档对比

用法: python tests/diag_win_detside.py <original-doc目录>
  环境变量 DIAG_STRIDE=6 抽样, DIAG_REPEAT=2 每档重复次数,
           DIAG_SIDES=736,512,384 档位.
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
    print("Windows det 精测: limit_side_len 降档 (耗时 vs 精度)")
    print("=" * 72)
    print("cpu_count:", os.cpu_count())

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr
    import glob as _glob

    real_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
    expanded = []
    for d in real_dirs:
        expanded.extend(_glob.glob(os.path.join(d, "*_assets")) or [d])
    blobs = load_blobs(expanded) if expanded else []
    if not blobs:
        print("!! 未找到图片")
        return
    stride = int(os.environ.get("DIAG_STRIDE", "6"))
    if stride > 1:
        blobs = blobs[::stride]
    repeat = int(os.environ.get("DIAG_REPEAT", "2"))
    sides = [int(x) for x in os.environ.get("DIAG_SIDES", "736,512,384").split(",")]
    print("图片: %d 张; 每档重复 %d 遍取最快; 档位: %s" % (len(blobs), repeat, sides))

    eng = ocr._engine_obj()
    det = eng.text_det

    def prep(img):
        im = eng.load_img(img)
        im, _, _ = eng.preprocess(im)
        im, _ = eng.maybe_add_letterbox(im, {})
        return im

    # 预先加载 (排除 IO / 解码影响)
    arrs = []
    for b in blobs:
        try:
            arrs.append(prep(b))
        except Exception:
            pass
    print("有效图: %d" % len(arrs))

    saved = det.limit_side_len
    print("-" * 72)
    header = "  side=%-4d  det=%6.2fs (%.3fs/图)  总框数=%-5d  平均框/图=%.1f"
    base_t = base_boxes = None
    for side in sides:
        det.limit_side_len = side
        best = None
        boxes_last = 0
        for _ in range(repeat):
            t = time.time()
            boxes_total = 0
            for im in arrs:
                try:
                    boxes, _ = det(im)
                    if boxes is not None:
                        boxes_total += len(boxes)
                except Exception:
                    pass
            dt = time.time() - t
            if best is None or dt < best:
                best = dt
            boxes_last = boxes_total
        if base_t is None:
            base_t, base_boxes = best, boxes_last
        print(header % (side, best, best / len(arrs), boxes_last, boxes_last / len(arrs)))
        print("           相对 736: 耗时 %.2fx, 框数 %+.1f%%" %
              (best / base_t, (boxes_last / base_boxes - 1) * 100 if base_boxes else 0))
    det.limit_side_len = saved
    print("=" * 72)
    print("判读: 若低档耗时显著下降(~0.5x)且框数变化很小(<5%) => 降分辨率是净收益,")
    print("      值得做成可配 (默认保持 736, 用户按需调低).")
    print("=" * 72)


if __name__ == "__main__":
    main()
