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


def main():
    print("=" * 70)
    print("Windows 诊断 v5: 完整引擎 x 不同 intra_op 的并发扩展")
    print("=" * 70)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    blobs = make_blobs(24)
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
