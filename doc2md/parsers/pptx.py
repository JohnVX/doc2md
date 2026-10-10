"""PowerPoint(pptx)解析: python-pptx, 递归分组图形 + 内嵌图提取+OCR占位.

通用:
  - 递归 walk 进 GROUP, 不丢分组内内容
  - 标题: slide.shapes.title -> 否则首个非空文本框
  - 文本框/表格/图片/备注; 跳过母版占位符样板文本
  - 图片 blob -> 存 assets -> OCR 占位(收尾执行)
"""
from pathlib import Path

from .. import util
from ._common import defer, is_placeholder, md_table


def parse(path, ctx=None):
    """pptx 解析入口(分组递归+图片OCR)."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(path)
    stem = Path(path).stem

    core_title = None
    try:
        core_title = prs.core_properties.title
    except Exception:
        pass
    first_slide_title = ""
    if prs.slides:
        try:
            first_slide_title = _slide_title(prs.slides[0])
        except Exception:
            first_slide_title = ""
    title = util.pick_title([first_slide_title, core_title], stem)

    out = []
    deferred = []
    for i, slide in enumerate(prs.slides, 1):
        out.append(_render_slide(i, slide, ctx, deferred))
    body = "\n\n".join(x for x in out if x) or f"<!-- 空 presentation: {Path(path).name} -->"
    return {"title": title, "body": body, "tags": [], "meta": {}, "deferred": deferred}


def _walk(shapes):
    """递归展开分组图形. 用 hasattr(sh,'shapes') 判 GroupShape, 不依赖
    模块级 MSO_SHAPE_TYPE(其在 parse() 内才 import, 否则 NameError 被吞致分组不递归)."""
    for sh in shapes:
        try:
            if hasattr(sh, "shapes"):
                yield from _walk(sh.shapes)
            else:
                yield sh
        except Exception:
            yield sh


def _slide_title(slide):
    """取幻灯片标题."""
    try:
        t = slide.shapes.title
        if t is not None and t.text.strip():
            return t.text.strip()
    except Exception:
        pass
    # 兜底: 首个非空文本框
    for sh in _walk(slide.shapes):
        if sh.has_text_frame and sh.text_frame.text.strip():
            return sh.text_frame.text.strip().splitlines()[0].strip()
    return ""


def _render_slide(idx, slide, ctx, deferred):
    """渲染一张幻灯片为 md."""
    title = _slide_title(slide)
    head = f"## Slide {idx}" + (f": {title}" if title else "")
    title_shape = None
    try:
        title_shape = slide.shapes.title
    except Exception:
        pass

    items = []
    img_no = 0
    unhandled = 0
    for sh in _walk(slide.shapes):
        if title_shape is not None and sh is title_shape:
            continue
        handled = False
        if sh.has_text_frame:
            txt = _fmt_textframe(sh.text_frame)
            if txt and not is_placeholder(txt):
                items.append(txt)
            handled = True
        if sh.has_table:
            items.append(_render_table(sh.table))
            handled = True
        try:
            if sh.shape_type == 13:  # PICTURE
                img_no += 1
                _handle_picture(sh, img_no, ctx, items, deferred)
                handled = True
        except Exception:
            pass
        if not handled:
            unhandled += 1
    if unhandled:
        items.append(defer(deferred, "chart", unhandled, "图表/SmartArt等未提取"))

    # 备注
    try:
        nt = slide.notes_slide.notes_text_frame.text.strip()
        if nt and not is_placeholder(nt):
            items.append(f"> 备注: {nt}")
    except Exception:
        pass

    return head + "\n\n" + "\n\n".join(items) if items else head


def _fmt_textframe(tf):
    """格式化文本框为 md 段落."""
    out = []
    for para in tf.paragraphs:
        t = "".join(r.text or "" for r in para.runs) or para.text
        t = t.strip()
        if not t:
            continue
        lvl = getattr(para, "level", 0) or 0
        if lvl > 0:
            out.append("  " * lvl + "- " + t)
        else:
            out.append(t)
    return "\n".join(out)


def _render_table(tbl):
    """渲染 pptx 表格为 md 表格."""
    try:
        ncols = len(tbl.columns)
    except Exception:
        ncols = max((len(r.cells) for r in tbl.rows), default=0)
    if ncols < 1 or not tbl.rows:
        return ""
    rows = []
    for r in tbl.rows:
        cells = [c.text.replace("\n", " ").replace("|", "\\|").strip() for c in r.cells]
        rows.append(cells)
    return md_table(rows, ncols)


def _handle_picture(sh, no, ctx, items, deferred):
    """处理图片形状: 提取 blob 存 assets, 插入 OCR 占位(OCR 在 pipeline 收尾执行).

    worker 进程不再加载 OCR 引擎, 避免多进程内存爆炸 (每进程 ~400MB).
    """
    try:
        blob = sh.image.blob
        ext = (sh.image.ext or "png").lstrip(".")
    except Exception:
        return
    dest, ref = util.save_asset(blob, ctx, ext)
    items.append(f"![图{no}]({ref})\n\n<!-- ocr:img:{dest} -->")
