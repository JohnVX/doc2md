"""解析器注册表.

约定每个 handler 签名: parse(path, ctx=None) -> dict
  返回: {"title": str, "body": str(markdown, 不含 front-matter), "tags": list, "meta": dict}
  ctx: 可选上下文, 常含 encoding / lang / output_root / doc_id / category / relpath 等.
"""
from . import md as _md
from . import text as _text
from . import image as _image
from . import xlsx as _xlsx
from . import docx as _docx
from . import pptx as _pptx
from . import pdf as _pdf

REGISTRY = {
    "md": _md.parse,
    "text": _text.parse,
    "image": _image.parse,
    "xlsx": _xlsx.parse,
    "docx": _docx.parse,
    "pptx": _pptx.parse,
    "pdf": _pdf.parse,
}


def get(handler_key):
    return REGISTRY.get(handler_key)


def supported():
    return set(REGISTRY)
