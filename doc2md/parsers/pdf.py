"""PDF 解析: PyMuPDF (文字流+大纲+表格+渲染扫描页).

通用:
  - 文字流: fitz blocks 按 (y,x) 排序 (改善多栏/阅读序)
  - 大纲: get_toc() 在对应页注入 # 级标题, 结构化 md
  - 表格: fitz find_tables(), 过滤伪表(列<2/行<2); 该表区文字从 blocks 剔除避免重复
  - find_tables() 抛异常时该页退化为仅文字层, 不整解析失败
  - 扫描页(无文字/表格/大纲): 渲染页为图 -> OCR
  - 文字页含内嵌图: 留 stage2 标记(图内文字未提取)
"""
from pathlib import Path

from .. import ocr, util
from ._common import defer, md_table

import logging
log = logging.getLogger("pdf")


def parse(path, ctx=None):
    """PDF 解析入口(fitz 文字+表格+大纲+扫描页)."""
    import pymupdf as fitz

    stem = Path(path).stem
    doc = None
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise RuntimeError(f"无法打开 PDF: {e}") from e
    try:
        deferred = []
        # 加密且需口令 -> 无法提取, defer
        if getattr(doc, "needs_pass", 0):
            log.warning("PDF 加密且需口令, 跳过文字提取: %s", path)
            return {"title": stem,
                    "body": defer(deferred, "encrypted", 1, f"PDF加密: {Path(path).name}"),
                    "tags": [], "meta": {}, "deferred": deferred}
        meta_title = (doc.metadata or {}).get("title") or ""
        title = util.pick_title([meta_title], stem)
        toc = doc.get_toc()
        # page(1-based) -> [(level, title), ...]
        page_entries = {}
        for lvl, t, pg in toc:
            page_entries.setdefault(pg, []).append((lvl, t))

        n = doc.page_count
        out = []
        for i in range(n):
            segs = []
            for lvl, t in page_entries.get(i + 1, []):
                lvl = max(1, min(6, lvl))
                segs.append((0.0, "head", "#" * lvl + " " + t))
            segs.extend(_page_segments(doc[i]))
            if not segs:
                # 无文字/表格/大纲 -> 扫描页, 渲染+OCR (不直接跳过)
                out.append(_ocr_scan_page(doc[i], ctx, deferred))
                continue
            segs.sort(key=lambda s: s[0])
            page_md = "\n\n".join(s[2] for s in segs if s[2])
            if not page_md.strip():
                page_md = _ocr_scan_page(doc[i], ctx, deferred)
            # 文字页含内嵌图(非整页扫描): 图内文字未提取, 留标记给 stage2
            # (不强行 OCR 避免 logo/底图加噪; 纯扫描页已由 _ocr_scan_page 处理)
            try:
                n_imgs = len(doc[i].get_images(full=False))
            except Exception:
                n_imgs = 0
            if n_imgs and page_md.strip():
                page_md += "\n\n" + defer(deferred, "inline-image", n_imgs,
                                          "图内文字未提取, 见原文件")
            out.append(page_md)
        body = "\n\n".join(x for x in out if x)
    finally:
        if doc:
            doc.close()

    if not body:
        body = f"<!-- 空 PDF / 全图无文字层: {Path(path).name} -->"
    return {"title": title, "body": body, "tags": [], "meta": {}, "deferred": deferred}


def _page_segments(fpage):
    """返回 [(y, type, md), ...] 该页的表格/文字/标题段."""
    segs = []
    # 表格 (fitz find_tables; 抛异常则该页无表格)
    tables = []
    try:
        for tb in fpage.find_tables().tables:
            ext = tb.extract()
            if not ext:
                continue
            rows = [[("" if c is None else str(c)) for c in r] for r in ext]
            nrows = len(rows)
            ncols = max((len(r) for r in rows), default=0)
            if nrows < 2 or ncols < 2:
                continue  # 过滤伪表 (页眉/单列等)
            tables.append((tb.bbox, rows, ncols))
    except Exception:
        pass

    table_bboxes = [t[0] for t in tables]

    # 文字块 (fitz), 排除落在表格 bbox 内的 (避免与表格重复)
    try:
        blocks = fpage.get_text("blocks")
    except Exception:
        blocks = []
    for b in blocks:
        x0, y0, x1, y1, text, _no, _bt = (list(b) + [None, None, None])[:7]
        if not text or not text.strip():
            continue
        if _in_any_table((x0, y0, x1, y1), table_bboxes):
            continue
        segs.append((y0, "text", text.strip()))

    # 表格段
    for bbox, rows, ncols in tables:
        segs.append((bbox[1], "table", md_table(rows, ncols)))
    return segs


def _in_any_table(bbox, table_bboxes, tol=2):
    """检查 bbox 是否落在任一表格区域内."""
    bx0, by0, bx1, by1 = bbox
    for tx0, ty0, tx1, ty1 in table_bboxes:
        if (bx0 + tol >= tx0 and bx1 - tol <= tx1
                and by0 + tol >= ty0 and by1 - tol <= ty1):
            return True
    return False


def _ocr_scan_page(fpage, ctx, deferred, dpi=150):
    """扫描页: 渲染为 png 存 assets -> OCR. 无引擎/无文字 -> defer 占位."""
    try:
        pix = fpage.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
    except Exception:
        return defer(deferred, "scan-page", 1, "渲染失败")
    dest, ref = util.save_asset(png, ctx, "png", prefix="scan")
    txt = ocr.ocr_image(png, ctx)
    if txt:
        return f"![扫描页]({ref})\n\n```\n{txt}\n```"
    return f"![扫描页]({ref})\n\n" + defer(deferred, "scan-page", 1, "OCR无结果")
