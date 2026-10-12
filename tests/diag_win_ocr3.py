"""Windows 决定性诊断: 直接驱动 doc2md 的真实 OCR 路径, 打印每张图的开始/结束时间.

这个脚本复现 _post_ocr 的确切逻辑 (ThreadPool + ocr.ocr_image), 并记录:
  - 每张图由哪个线程处理
  - 每张图的开始/结束时间戳
  - 实际并发度 (同时有多少张图在跑)

如果时间戳显示任务不重叠 => 真串行, 问题在 GIL/调度/锁.
如果重叠但每张变慢 => 资源争抢 (超订/内存带宽).

用法: python tests/diag_win_ocr3.py [资产目录]
默认用 /tmp/test 产物 (若不存在则生成合成图).
"""
import glob
import os
import sys
import threading
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")

# 记录并发度
_active = {"n": 0, "max": 0}
_alock = threading.Lock()
_events = []  # (thread_name, event, t)


def _mark(ev):
    with _alock:
        _events.append((threading.current_thread().name, ev, time.time()))


def find_assets(argv):
    roots = list(argv[1:])
    if not roots:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(here)
        roots = [os.path.join(repo, "knowledge", "original-doc"),
                 "/tmp/test/knowledge/original-doc"]
    files = []
    for r in roots:
        for d in glob.glob(os.path.join(r, "*_assets")):
            files.extend(sorted(glob.glob(os.path.join(d, "*"))))
    return files


def ocr_supported(path):
    try:
        with open(path, "rb") as fh:
            b = fh.read(44)
    except Exception:
        return False
    if len(b) < 4:
        return False
    if b[:4] == b"\xd7\xcd\xc6\x9a":
        return False
    if b[:4] == b"\x01\x00\x00\x00" and len(b) > 44 and b[40:44] == b" EMF":
        return False
    return True


def main():
    print("=" * 70)
    print("Windows 决定性诊断 v3: doc2md 真实 OCR 路径的并发度")
    print("=" * 70)
    print("cpu_count:", os.cpu_count())

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr

    files = [f for f in find_assets(sys.argv) if ocr_supported(f)]
    blobs = [open(f, "rb").read() for f in files]
    print("测试图片:", len(blobs), "张")
    print("ocr.available():", ocr.available())

    # 预热引擎
    t = time.time()
    ocr.ocr_image(blobs[0])
    print("首图(含引擎初始化)耗时: %.2fs" % (time.time() - t))

    # 检查真实 session 的 intra_op
    try:
        so = ocr._engine_obj().text_det.infer.session.get_session_options()
        print("DET intra_op =", so.intra_op_num_threads,
              " inter_op =", so.inter_op_num_threads)
    except Exception as e:
        print("intra_op 检查失败:", e)

    from concurrent.futures import ThreadPoolExecutor

    def timed_one(blob):
        with _alock:
            _active["n"] += 1
            _active["max"] = max(_active["max"], _active["n"])
        _mark("start")
        t0 = time.time()
        ocr.ocr_image(blob)
        dt = time.time() - t0
        _mark("end")
        with _alock:
            _active["n"] -= 1
        return dt

    for nw in (1, 4, 12):
        _active["n"] = 0
        _active["max"] = 0
        _events.clear()
        t = time.time()
        with ThreadPoolExecutor(max_workers=nw) as p:
            durs = list(p.map(timed_one, blobs))
        dt = time.time() - t
        # 计算并发度: 事件排序后扫描
        evs = sorted(_events, key=lambda x: x[2])
        cur = mx = 0
        for _, ev, _ in evs:
            cur += 1 if ev == "start" else -1
            mx = max(mx, cur)
        print("-" * 70)
        print("threads=%-3d  总 %.1fs  %.3fs/img  speedup_vs_serial" % (nw, dt, dt / len(blobs)))
        print("  实测最大并发度: %d  (期望接近 %d)" % (mx, nw))
        print("  单图耗时: min %.2f  median %.2f  max %.2f" %
              (min(durs), sorted(durs)[len(durs) // 2], max(durs)))

    print("=" * 70)


if __name__ == "__main__":
    main()
