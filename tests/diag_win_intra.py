"""Windows 诊断 v5: 完整 rapidocr 引擎在不同 intra_op 下的并发扩展.

背景: 纯 ONNX session 并发 7.3x (diag B), 但完整引擎只能 ~2.5x (diag4).
怀疑 intra_op=1 对"多次小 run + Python 胶水"过激; 或 GIL 胶水占比高.

本脚本用完整引擎 (det+cls+rec) 跑真实并发, 扫 intra_op = 1/2/4/default(-1),
每个值测 1/4/8/12 线程, 找出并发吞吐最优的 intra_op.

用法: python tests/diag_win_intra.py
"""
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")


def make_blobs(n=24, h=800, w=1100):
    import numpy as np
    import cv2
    out = []
    for k in range(n):
        img = np.full((h, w, 3), 255, np.uint8)
        for i in range(12):
            cv2.putText(img, "Doc %d line %d ABCDEF" % (k, i),
                        (40, 50 + i * 60), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (0, 0, 0), 2)
        ok, buf = cv2.imencode(".png", img)
        out.append(buf.tobytes())
    return out


def load_real_blobs(dirs):
    """从真实资产目录加载可 OCR 图片的 bytes."""
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
    print("Windows 诊断 v5: 完整引擎 x 不同 intra_op 的并发扩展")
    print("=" * 70)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # 优先用真实图片目录 (命令行参数), 否则合成图
    import glob as _glob
    real_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
    if not real_dirs:
        # 默认找 knowledge/original-doc/*_assets
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(here)
        real_dirs = _glob.glob(os.path.join(repo, "knowledge", "original-doc", "*_assets"))
        real_dirs += _glob.glob("/tmp/test/knowledge/original-doc/*_assets")
    blobs = load_real_blobs(real_dirs) if real_dirs else []
    if blobs:
        print("数据源: 真实图片 %d 张 (%d 目录)" % (len(blobs), len(real_dirs)))
    else:
        blobs = make_blobs(24)
        print("数据源: 合成图 %d 张" % len(blobs))

    from concurrent.futures import ThreadPoolExecutor

    for intra in [1, 2, 4, -1]:
        os.environ["DIAG_INTRA"] = str(intra)
        from rapidocr_onnxruntime import RapidOCR
        if intra == -1:
            eng = RapidOCR()  # 默认 (全核)
        else:
            eng = RapidOCR(intra_op_num_threads=intra, inter_op_num_threads=1)
        try:
            so = eng.text_det.infer.session.get_session_options()
            real = so.intra_op_num_threads
        except Exception:
            real = "?"
        eng(blobs[0])  # 预热
        print("-" * 70)
        print("intra_op=%s (实际=%s)" % (intra, real))
        base = None
        for nw in [1, 4, 8, 12]:
            t = time.time()
            with ThreadPoolExecutor(max_workers=nw) as p:
                list(p.map(eng, blobs))
            dt = time.time() - t
            if base is None:
                base = dt
            print("  threads=%-3d  %6.2fs  %.3fs/img  speedup=%.2fx" %
                  (nw, dt, dt / len(blobs), base / dt))
        del eng

    print("=" * 70)
    print("判读: 找 speedup@12 最大的 intra_op; 若都 ~2.5x 则瓶颈是 GIL 胶水.")
    print("=" * 70)


if __name__ == "__main__":
    main()
