"""Windows 诊断 v4: PIL 解码是否吃 GIL 并限制并行度.

对比三种负载在 1/4/8/12 线程下的扩展:
  A. 纯解码 (bytes -> ndarray), 不调用 ONNX
  B. 纯 ONNX (ndarray -> OCR 但跳过 decode: 直接喂 ndarray)
  C. 解码 + ONNX (bytes -> OCR, doc2md 现状)

若 A 扩展差 => PIL 解码吃 GIL, 是瓶颈.
若 C 远差于 B => 印证 decode 拖累并行.

用法: python tests/diag_win_decode.py
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
    print(label)
    base = None
    for nw in counts:
        t = time.time()
        with ThreadPoolExecutor(max_workers=nw) as p:
            list(p.map(fn, items))
        dt = time.time() - t
        if base is None:
            base = dt
        print("  threads=%-3d  %6.2fs  %.3fs/img  speedup=%.2fx" %
              (nw, dt, dt / len(items), base / dt))


def main():
    print("=" * 70)
    print("Windows 诊断 v4: PIL 解码 vs ONNX 的并行度")
    print("=" * 70)
    print("cpu_count:", os.cpu_count())
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)

    blobs = make_blobs(24)
    counts = [1, 4, 8, 12]

    # --- A. 纯解码 (bytes -> ndarray) ---
    from io import BytesIO
    import numpy as np
    from PIL import Image

    def decode_only(b):
        im = Image.open(BytesIO(b))
        return np.array(im)

    decode_only(blobs[0])
    curve(decode_only, blobs, counts, "A. 纯 PIL 解码 (bytes -> ndarray)")

    # --- 准备 ndarray ---
    arrs = [decode_only(b) for b in blobs]

    # --- 注册 doc2md 的引擎 (intra_op=1) ---
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr
    eng = ocr._engine_obj()
    # 用 ndarray 预热
    eng(arrs[0])

    # --- B. 纯 ONNX (ndarray -> OCR, rapidocr 对 ndarray 不再解码) ---
    def onnx_only(a):
        return eng(a)

    curve(onnx_only, arrs, counts, "B. 纯 ONNX (ndarray -> OCR, 跳过 PIL 解码)")

    # --- C. 解码 + ONNX (doc2md 现状: bytes -> ocr_image) ---
    def full(b):
        return ocr.ocr_image(b)

    curve(full, blobs, counts, "C. 解码+ONNX (bytes -> ocr_image, doc2md 现状)")

    print("=" * 70)
    print("判读: 若 A 扩展差 => PIL 解码吃 GIL 是瓶颈;")
    print("      若 B 远好于 C => 预解码能显著提速.")
    print("=" * 70)


if __name__ == "__main__":
    main()
