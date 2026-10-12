"""图片解析: 存图+插 OCR 占位, OCR 留到 pipeline 收尾执行.

独立图片文件会被 pipeline 拷到 original-doc/<relpath>; parser 把图 blob 存到
assets 并插入 <!-- ocr:img:dest --> 占位. OCR 在 _post_ocr (parent 进程) 执行,
与其他 parser (docx/pptx/pdf) 一致, worker 进程不加载 OCR 引擎.
"""
import os
from pathlib import Path

from .. import util
from ._common import defer


def parse(path, ctx=None):
    """图片解析入口: 存 assets + OCR 占位 (OCR 在 pipeline 收尾执行)."""
    ctx = ctx or {}
    stem = Path(path).stem
    rel = ctx.get("relpath") or os.path.basename(path)
    ref = f"../../original-doc/{rel}"

    deferred = []
    try:
        blob = Path(path).read_bytes()
    except Exception:
        return {"title": stem, "body": f"![{stem}]({ref})\n\n" + defer(deferred, "parse-failed", 1, "读取图片失败") + "\n",
                "tags": [], "meta": {}, "deferred": deferred}

    dest, _ = util.save_asset(blob, ctx, "png", prefix="img")
    if dest:
        body = f"![{stem}]({ref})\n\n<!-- ocr:img:{dest} -->"
    else:
        body = f"![{stem}]({ref})\n\n" + defer(deferred, "image-ocr", 1, "无引擎或纯图无文字") + "\n"
    return {"title": stem, "body": body, "tags": [], "meta": {}, "deferred": deferred}
