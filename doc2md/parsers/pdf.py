"""PDF 解析: PyMuPDF (文字流+大纲+表格+渲染扫描页).

通用:
  - 文字流: fitz blocks 按 (y,x) 排序 (改善多栏/阅读序)
  - 大纲: get_toc() 在对应页注入 # 级标题, 结构化 md
  - 表格: fitz find_tables(), 过滤伪表(列<2/行<2); 该表区文字从 blocks 剔除避免重复
  - find_tables() 抛异常时该页退化为仅文字层, 不整解析失败
  - 扫描页(无文字/表格/大纲): 渲染页为图 -> 存 assets -> OCR 占位(收尾执行)
  - 文字页含内嵌图: 留 stage2 标记(图内文字未提取)

不使用 pymupdf_layout (PyMuPDF 推广的增强布局分析包):
  - 多栏阅读序/无框表格/版面分区属语义理解, 交 stage2 多模态大模型处理
  - stage1 原则: 确定性提取 + 诚实标记; 文字从不丢失(顺序/格式不完美但内容完整)
  - 该包 API 稳定性/跨平台/维护活跃度未知, 加不确定依赖违背确定性设计意图
"""
import contextlib
import io
import logging
from pathlib import Path

from .. import util
from ._common import defer, md_table

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
        toc = doc.get_toc()
        # page(1-based) -> [(level, title), ...]
        page_entries = {}
        for lvl, t, pg in toc:
            page_entries.setdefault(pg, []).append((lvl, t))

        n = doc.page_count
        boilerplate = _find_boilerplate(doc)
        if boilerplate:
            log.info("PDF 检出 %d 个跨页样板 block (页眉/页脚/水印), 已跳过: %s",
                     len(boilerplate), Path(path).name)
        out = []
        for i in range(n):
            segs = []
            for lvl, t in page_entries.get(i + 1, []):
                lvl = max(1, min(6, lvl))
                segs.append((0.0, "head", "#" * lvl + " " + t))
            segs.extend(_page_segments(doc[i], boilerplate))
            if not segs:
                # 无文字/表格/大纲 -> 扫描页, 渲染+OCR (不直接跳过)
                # 例外: 该页本有文字层(如只剩页眉/水印被剔除) -> 视为空页,
                # 不渲染扫描页 (否则封面/分隔页会凭空产生 ![扫描页] + OCR 开销)
                if _has_text_layer(doc[i]):
                    continue
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
                                          "图内文字未提取, 见原文件",
                                          loc=f"p{i + 1}")
            out.append(page_md)
        body = "\n\n".join(x for x in out if x)
    finally:
        if doc:
            doc.close()

    if not body:
        body = f"<!-- 空 PDF / 全图无文字层: {Path(path).name} -->"
    # 标题需正文参与判定 (过滤不可信的元数据占位标题, 如 "幻灯片 1")
    title = util.pick_title([meta_title], stem, body=body)
    return {"title": title, "body": body, "tags": [], "meta": {}, "deferred": deferred}


def _find_boilerplate(doc, min_ratio=0.6, min_pages=3):
    """检出跨页重复的样板 block (页眉/页脚/水印).

    判据: (归一化文字, 取整 bbox) 在 >= max(min_pages, 页数*min_ratio) 页上完全相同.
    正常正文不可能在多页一字不差地出现在同一位置; 而页眉/水印/页脚必然如此.
    返回 set[(text, x0, y0, x1, y1)].
    """
    from collections import Counter
    cnt = Counter()
    total = doc.page_count
    for i in range(total):
        seen = set()
        try:
            blocks = doc[i].get_text("blocks")
        except Exception:
            continue
        for b in blocks:
            x0, y0, x1, y1, text = (list(b) + [None] * 5)[:5]
            t = " ".join((text or "").split())
            if not t:
                continue
            key = (t, round(x0), round(y0), round(x1), round(y1))
            if key not in seen:
                seen.add(key)
                cnt[key] += 1
    thr = max(min_pages, total * min_ratio)
    return {k for k, c in cnt.items() if c >= thr}


def _is_boilerplate_block(b, boilerplate):
    """该 block 是否命中样板集."""
    x0, y0, x1, y1, text = (list(b) + [None] * 5)[:5]
    t = " ".join((text or "").split())
    if not t:
        return False
    return (t, round(x0), round(y0), round(x1), round(y1)) in boilerplate


def _page_segments(fpage, boilerplate=None):
    """返回 [(y, type, md), ...] 该页的表格/文字/标题段."""
    segs = []
    # 表格 (fitz find_tables; 抛异常则该页无表格)
    # redirect_stdout: 压掉 PyMuPDF 推广 pymupdf_layout 的 stdout 信息 (见模块 docstring)
    tables = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for tb in fpage.find_tables().tables:
                # 用 rows[].cells[] (坐标) 自己重建单元格文本 (而非 tb.extract() 直接取文本).
                # 原因: 水印/页眉文字与表格 bbox 重叠时, extract() 会把横穿的
                # 水印字符混进单元格 (如 g_aaaBbb -> "g aaaBbb _", 下划线错位);
                # 只取"完全落在单元格内且行高正常"的 word, 避开旋转水印.
                tb_rows = getattr(tb, "rows", None)
                if not tb_rows:
                    continue
                rows = []
                for tr in tb_rows:
                    cells = [_cell_text(fpage, c) if c is not None else ""
                             for c in tr.cells]
                    rows.append(cells)
                nrows = len(rows)
                ncols = max((len(r) for r in rows), default=0)
                if nrows < 2 or ncols < 2:
                    continue  # 过滤伪表 (页眉/单列等)
                tables.append((tb.bbox, rows, ncols))
    except Exception:
        pass

    table_bboxes = [t[0] for t in tables]

    # 文字块 (fitz), 排除落在表格 bbox 内的 (避免与表格重复) 与样板(水印/页眉页脚)
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
        if boilerplate and _is_boilerplate_block(b, boilerplate):
            continue
        segs.append((y0, "text", text.strip()))

    # 表格段
    for bbox, rows, ncols in tables:
        segs.append((bbox[1], "table", md_table(rows, ncols)))
    return segs


def _cell_text(fpage, clip_bbox, max_word_h=120, h_ratio=3.5):
    """用 words 重建单元格文本, 只取完全落在 bbox 内的 word.

    clip_bbox: fitz.Rect 或 (x0,y0,x1,y1).
    水印过滤: 旋转/倾斜的水印 word bbox 很高. 主判据为**相对高度**:
    word 高 > 同格 word 高度中位数 x h_ratio 者视为水印剔除 —— 自适应字号,
    不误删大字号表头(实测大字号约 2.5x 中位数, 水印达 7x以上).
    max_word_h 为绝对安全阀, 防异常极端(整格仅水印时中位数被拉高, 相对判据失效),
    取宽松值 120, 不阻挡合法大字号文字.
    返回按行聚合(行内按 x 排序)的文本.
    """
    try:
        import pymupdf as fitz
        clip = clip_bbox if hasattr(clip_bbox, "x0") else fitz.Rect(clip_bbox)
    except Exception:
        return ""
    inside = []
    try:
        words = fpage.get_text("words", clip=clip)
    except Exception:
        return ""
    for w in words:
        x0, y0, x1, y1, text = (list(w) + [None] * 5)[:5]
        if not text:
            continue
        if (x0 + 1 >= clip.x0 and x1 - 1 <= clip.x1
                and y0 + 1 >= clip.y0 and y1 - 1 <= clip.y1):
            inside.append((y1 - y0, y0, x0, text))
    if not inside:
        return ""
    hs = sorted(h for h, _, _, _ in inside)
    med = hs[len(hs) // 2]
    cap = min(max_word_h, max(med * h_ratio, med + 1))
    ws = [(y, x, text) for h, y, x, text in inside if h <= cap]
    if not ws:
        return ""
    ws.sort()
    lines = []
    cur = []
    lasty = None
    for y, x, text in ws:
        if lasty is None or abs(y - lasty) <= 3:
            cur.append((x, text))
        else:
            cur.sort()
            lines.append(" ".join(t for _, t in cur))
            cur = [(x, text)]
        lasty = y
    if cur:
        cur.sort()
        lines.append(" ".join(t for _, t in cur))
    return "\n".join(lines)


def _has_text_layer(fpage):
    """该页是否存在任何文字层 block (含被剔除的样板).

    用于区分"本就是扫描页(无文字层)"与"文字全是样板而变空"的页.
    """
    try:
        for b in fpage.get_text("blocks"):
            text = (list(b) + [None] * 5)[4]
            if text and text.strip():
                return True
    except Exception:
        pass
    return False


def _in_any_table(bbox, table_bboxes, tol=2):
    """检查 bbox 是否落在任一表格区域内."""
    bx0, by0, bx1, by1 = bbox
    for tx0, ty0, tx1, ty1 in table_bboxes:
        if (bx0 + tol >= tx0 and bx1 - tol <= tx1
                and by0 + tol >= ty0 and by1 - tol <= ty1):
            return True
    return False


def _ocr_scan_page(fpage, ctx, deferred, dpi=150):
    """扫描页: 渲染为 png 存 assets. OCR 在 pipeline 收尾执行."""
    try:
        pix = fpage.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
    except Exception:
        return defer(deferred, "scan-page", 1, "渲染失败")
    dest, ref = util.save_asset(png, ctx, "png", prefix="scan")
    if not dest:
        return defer(deferred, "scan-page", 1, "扫描页未存盘")
    return f"![扫描页]({ref})\n\n<!-- ocr:scan:{dest} -->"
