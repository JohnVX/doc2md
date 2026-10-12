"""真实负载 OCR 并行基准 (用 /tmp/test 产物或指定资产目录).

用法:
    python tests/bench_ocr_real.py [资产目录...]

默认扫描 <repo>/knowledge/original-doc/*_assets 或 /tmp/test/knowledge/original-doc/*_assets.

测量: 用真实图片, 跑 rapidocr eng(), 线程数 1/2/4/8/12/16..., 输出耗时+加速比.
同时报告: 图片数/面积分档/有无文字, 便于对照.

这是"合成图之外"的真实数据, 与 diag_win_ocr.py 互补.
"""
import glob
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")


def find_assets(argv):
    roots = list(argv[1:])
    if not roots:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(here)
        roots = [
            os.path.join(repo, "knowledge", "original-doc"),
            "/tmp/test/knowledge/original-doc",
        ]
    files = []
    for r in roots:
        for d in glob.glob(os.path.join(r, "*_assets")):
            files.extend(sorted(glob.glob(os.path.join(d, "*"))))
    return files


def ocr_supported(path):
    """镜像 doc2md.ocr.is_ocr_supported: 跳过 WMF/EMF."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read(44)
    except Exception:
        return False
    if len(blob) < 4:
        return False
    if blob[:4] == b"\xd7\xcd\xc6\x9a":
        return False
    if blob[:4] == b"\x01\x00\x00\x00" and len(blob) > 44 and blob[40:44] == b" EMF":
        return False
    return True


def main():
    files = [f for f in find_assets(sys.argv) if ocr_supported(f)]
    if not files:
        print("找不到资产图片. 用法: python tests/bench_ocr_real.py <资产目录>")
        sys.exit(1)
    print("=" * 70)
    print("真实负载基准: %d 张图" % len(files))
    print("=" * 70)

    # 面积分档
    try:
        from PIL import Image
        tiny = small = big = 0
        for f in files:
            try:
                w, h = Image.open(f).size
            except Exception:
                continue
            a = w * h
            if a < 200 * 200:
                tiny += 1
            elif a < 500 * 500:
                small += 1
            else:
                big += 1
        print("面积分档: tiny(<200²)=%d small(<500²)=%d big=%d" % (tiny, small, big))
    except Exception as e:
        print("分档失败:", e)

    from concurrent.futures import ThreadPoolExecutor
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    eng(files[0])  # warmup

    cpu = os.cpu_count() or 1
    counts = [1, 2, 4, 8, 12, 16, cpu]
    counts = sorted(set(c for c in counts if 1 <= c <= max(cpu, 2)))
    base = None
    print("-" * 70)
    for nw in counts:
        t = time.time()
        with ThreadPoolExecutor(max_workers=nw) as p:
            list(p.map(eng, files))
        dt = time.time() - t
        if base is None:
            base = dt
        print("  threads=%-3d  %6.2fs  %.3fs/img  speedup=%.2fx" %
              (nw, dt, dt / len(files), base / dt))
    print("=" * 70)


if __name__ == "__main__":
    main()
