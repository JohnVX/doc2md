"""Windows det side 精度验证: limit_side_len 736 vs 384 的文字内容差异.

目的: 若精度损失可接受, 直接把 det limit_side_len 固定为 384 (提速 4x).
逐图对比两个档位下完整 OCR (det+cls+rec) 的输出文字, 找出差异.

用法: python tests/diag_win_detdiff.py <original-doc目录>
  环境变量 DIAG_STRIDE=6 抽样.
"""
import difflib
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")


def load_blobs(dirs):
    import glob
    out = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*"))):
            if f.lower().endswith((".emf", ".wmf")):
                continue
            try:
                b = open(f, "rb").read()
            except Exception:
                continue
            if len(b) >= 4 and b[:4] != b"\xd7\xcd\xc6\x9a":
                out.append((os.path.basename(f), b))
    return out


def ocr_text(eng, blob):
    """完整 OCR, 返回拼接文字与统计."""
    try:
        res, elapse = eng(blob)
    except Exception as e:
        return None, 0, "ERR:%s" % e
    if not res:
        return "", 0, ""
    lines = []
    for item in res:
        # rapidocr 返回 [box, text, score]
        txt = item[1] if len(item) > 1 else ""
        lines.append(txt)
    joined = "".join(lines)
    return joined, len(lines), ""


def main():
    print("=" * 72)
    print("Windows det side 精度验证: 736 vs 384 文字内容差异")
    print("=" * 72)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from doc2md import ocr
    import glob as _glob

    real_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
    expanded = []
    for d in real_dirs:
        expanded.extend(_glob.glob(os.path.join(d, "*_assets")) or [d])
    pairs = load_blobs(expanded) if expanded else []
    if not pairs:
        print("!! 未找到图片")
        return
    stride = int(os.environ.get("DIAG_STRIDE", "6"))
    if stride > 1:
        pairs = pairs[::stride]
    print("图片: %d 张" % len(pairs))

    eng = ocr._engine_obj()
    det = eng.text_det
    saved = det.limit_side_len

    results = {}
    for side in [736, 384]:
        det.limit_side_len = side
        eng(pairs[0][1])  # 预热
        per_img = []
        t = time.time()
        for name, blob in pairs:
            txt, nlines, err = ocr_text(eng, blob)
            per_img.append((name, txt, nlines, err))
        dt = time.time() - t
        results[side] = per_img
        total_chars = sum(len(x[1]) for x in per_img if x[1])
        total_lines = sum(x[2] for x in per_img)
        print("-" * 72)
        print("side=%d: 总耗时 %.1fs, 总字符 %d, 总行数 %d" %
              (side, dt, total_chars, total_lines))
    det.limit_side_len = saved

    # --- 逐图对比 ---
    print("=" * 72)
    print("逐图差异 (736 -> 384):")
    r736 = {n: (t, l, e) for n, t, l, e in results[736]}
    r384 = {n: (t, l, e) for n, t, l, e in results[384]}
    lost_chars = gained_chars = 0
    changed_imgs = 0
    for name, _ in pairs:
        t36, l36, e36 = r736.get(name, ("", 0, ""))
        t84, l84, e84 = r384.get(name, ("", 0, ""))
        if t36 == t84 and l36 == l84:
            continue
        changed_imgs += 1
        dl = l84 - l36
        dc = len(t84) - len(t36)
        lost_chars += max(0, len(t36) - len(t84))
        gained_chars += max(0, len(t84) - len(t36))
        print("  [%s] 行 %d->%d (%+d), 字符 %d->%d (%+d)" %
              (name, l36, l84, dl, len(t36), len(t84), dc))
        if e36 or e84:
            print("     err736=%r err384=%r" % (e36, e84))
        # 打印具体差异 (384 相对 736 丢了/变了什么)
        sm = difflib.SequenceMatcher(None, t36, t84)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            old = t36[i1:i2]
            new = t84[j1:j2]
            if op == "delete":
                print("     - 736独有(384丢失): %r" % old)
            elif op == "insert":
                print("     + 384新增: %r" % new)
            else:  # replace
                print("     ~ 736[%r] -> 384[%r]" % (old, new))
    print("-" * 72)
    print("变化图: %d/%d; 净字符变化: %+d (丢 %d, 增 %d)" %
          (changed_imgs, len(pairs), gained_chars - lost_chars, lost_chars, gained_chars))
    print("=" * 72)
    print("判读: 若 736 与 384 文字基本一致 (变化图极少/仅个别字符) => 可固定 384.")
    print("      若 384 明显丢字 (丢字符远大于增) => 不宜固定, 保留 736.")
    print("=" * 72)


if __name__ == "__main__":
    main()
