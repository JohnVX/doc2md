"""图片解析: rapidocr OCR, 无文字则 defer 到 stage2.

独立图片文件会被 pipeline 拷到 original-doc/<relpath>; 正文里用相对引用指回原图,
并把 OCR 文本块附在引用后. 无引擎/无文字 -> stage2 占位.
"""
import os
from pathlib import Path

from .. import ocr
from ._common import defer


def parse(path, ctx=None):
    """图片解析入口(OCR 或 defer)."""
    ctx = ctx or {}
    stem = Path(path).stem
    rel = ctx.get("relpath") or os.path.basename(path)
    ref = f"../../original-doc/{rel}"

    deferred = []
    txt = ocr.ocr_image(path, ctx)
    if txt:
        body = f"![{stem}]({ref})\n\n<!-- ocr: rapidocr-onnxruntime -->\n\n```\n{txt}\n```\n"
    else:
        body = f"![{stem}]({ref})\n\n" + defer(deferred, "image-ocr", 1, "无引擎或纯图无文字") + "\n"
    return {"title": stem, "body": body, "tags": [], "meta": {}, "deferred": deferred}
