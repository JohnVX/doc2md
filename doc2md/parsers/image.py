"""图片解析: 存图+插 OCR 占位, OCR 留到 pipeline 收尾执行.

独立图片文件会被 pipeline 拷到 original-doc/<relpath>; parser 把图 blob 存到
assets 并插入 <!-- ocr:img:dest --> 占位. OCR 在 _post_ocr (parent 进程) 执行,
与其他 parser (docx/pptx/pdf) 一致, worker 进程不加载 OCR 引擎.
"""
import os
from pathlib import Path

from .. import util
from ._common import defer, guess_image_class


def parse(path, ctx=None):
    """图片解析入口: 存 assets + OCR 占位 (OCR 在 pipeline 收尾执行).

    矢量图 (EMF/WMF/SVG): 先渲染为 PNG 再存 (原始矢量多模态模型读不了);
    无法渲染则存原矢量 + 标记 (由 stage2 处理).
    """
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

    # 矢量图渲染由 util.save_asset 统一处理 (存 PNG 或保留原矢量)
    dest, _ = util.save_asset(blob, ctx, (Path(path).suffix.lstrip(".") or "png"), prefix="img")
    if dest:
        body = f"![{stem}]({ref})\n\n<!-- ocr:img:{dest} -->"
    else:
        body = f"![{stem}]({ref})\n\n" + defer(deferred, "image-ocr", 1, "无引擎或纯图无文字",
                                                cls=guess_image_class(blob, stem)) + "\n"
    return {"title": stem, "body": body, "tags": [], "meta": {}, "deferred": deferred}
