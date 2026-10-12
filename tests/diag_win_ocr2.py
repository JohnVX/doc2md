"""Windows 定向诊断 v2: 找出为什么 doc2md 的 OCR 是串行的.

关键问题:
  - diag B (纯 ONNX session) 在 Windows: 16 threads = 7.3x (线性)
  - diag C (eng(ndarray)) 第二次跑: 8 threads = 6.17x (线性)
  - 但 doc2md 实际: 12 threads, 156 图 = 0.6 图/s = 几乎串行!

本脚本精确复现 doc2md 的调用路径, 逐层剥离:
  1. eng(ndarray)              -- 最快路径
  2. eng(bytes)                -- doc2md 用的路径 (含 PIL 解码)
  3. ocr.ocr_image(bytes)      -- doc2md 的封装 (含 is_ocr_supported, _engine_obj)

每种都测 1/4/8/12 线程, 看哪种退化为串行.

在 Windows 上: python tests/diag_win_ocr2.py
请贴回完整输出.
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


def curve(fn, items, counts, label):
    from concurrent.futures import ThreadPoolExecutor
    print("-" * 70)
    print("曲线:", label)
    base = None
    for nw in counts:
        t = time.time()
        try:
            with ThreadPoolExecutor(max_workers=nw) as p:
                list(p.map(fn, items))
        except Exception as e:
            print("  threads=%-3d FAIL: %s" % (nw, e))
            continue
        dt = time.time() - t
        if base is None:
            base = dt
        print("  threads=%-3d  %6.2fs  %.3fs/img  speedup=%.2fx" %
              (nw, dt, dt / len(items), base / dt))


def main():
    print("=" * 70)
    print("Windows OCR 定向诊断 v2")
    print("=" * 70)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    n = 24
    blobs = make_blobs(n)
    counts = [1, 4, 8, 12]

    # --- 1. 原始 eng(ndarray) ---
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    import cv2
    arrs = [cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR) for b in blobs]
    eng(arrs[0])
    curve(eng, arrs, counts, "1. eng(ndarray) [最快路径]")

    # --- 2. eng(bytes) ---
    eng(blobs[0])
    curve(eng, blobs, counts, "2. eng(bytes) [含 PIL 解码]")

    # --- 3. doc2md.ocr.ocr_image(bytes) ---
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr
    print("ocr.available():", ocr.available())
    ocr.ocr_image(blobs[0])
    curve(ocr.ocr_image, blobs, counts, "3. ocr.ocr_image(bytes) [doc2md 封装]")

    # --- 4. 检查 intra_op 是否真的被强制为 1 ---
    print("-" * 70)
    print("4. intra_op 检查 (doc2md 封装后的真实 session)")
    try:
        det_sess = ocr._engine_obj().text_det.infer.session
        so = det_sess.get_session_options()
        print("  DET intra_op_num_threads =", so.intra_op_num_threads)
        print("  DET inter_op_num_threads =", so.inter_op_num_threads)
    except Exception as e:
        print("  检查失败:", e)

    # --- 5. GIL 检查: 忙循环计数线程能否在 OCR 期间运行 ---
    print("-" * 70)
    print("5. GIL 释放检查 (忙循环线程在并发 OCR 期间的自旋次数)")
    import threading
    stop = [False]
    spin = [0]

    def counter():
        while not stop[0]:
            spin[0] += 1

    th = threading.Thread(target=counter)
    th.start()
    time.sleep(0.2)
    base = spin[0]
    t = time.time()
    for b in blobs[:8]:
        ocr.ocr_image(b)
    dt = time.time() - t
    spins = spin[0] - base
    stop[0] = True
    th.join()
    print("  8 图串行耗时 %.2fs, 计数线程自旋 %d 次" % (dt, spins))
    print("  (自旋多 => GIL 被释放; 自旋少 => GIL 被占用)")

    print("=" * 70)
    print("完成.")
    print("=" * 70)


if __name__ == "__main__":
    main()
