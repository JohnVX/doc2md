"""Markdown 解析: 通用 pass-through + 标题取首 H1.

跟进内嵌本地图片引用 (![](相对路径)): 若解析得到本地文件, 则 OCR 并把文本块附在引用后;
并将该图登记到 meta.side_files 供 pipeline 一并拷贝到 assets (保 stage2 兜底).
"""
import os
import re
from pathlib import Path

from .. import ocr
from ._common import defer

_IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def parse(path, ctx=None):
    ctx = ctx or {}
    enc = ctx.get("encoding") or "utf-8"
    text = Path(path).read_text(encoding=enc, errors="replace")

    title = Path(path).stem
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("# ") and not s.startswith("## "):
            title = s[2:].strip()
            break

    body = text if text.endswith("\n") else text + "\n"

    side_files = []
    deferred = []
    base_dir = os.path.dirname(os.path.abspath(path))
    doc_id = ctx.get("doc_id", "doc")
    root = ctx.get("output_root")
    if root:
        asset_ref_prefix = f"../../original-doc/{doc_id}_assets/"

        def repl(m):
            alt, tgt = m.group(1), m.group(2).strip().split(" ")[0]
            if "://" in tgt or tgt.lower().startswith("data:") or tgt.startswith("#"):
                return m.group(0)
            cand = os.path.normpath(os.path.join(base_dir, tgt))
            if not os.path.isfile(cand):
                return m.group(0)
            seq = len(side_files) + 1
            dest = f"{seq:02d}_{os.path.basename(tgt)}"
            side_files.append((cand, dest))
            txt = ocr.ocr_image(cand, ctx)
            if txt:
                block = f"\n\n<!-- image-ocr: {dest} -->\n```\n{txt}\n```\n"
            else:
                block = "\n\n" + defer(deferred, "image-ocr", 1, dest) + "\n"
            return f"![{alt}]({asset_ref_prefix}{dest})" + block

        body = _IMG_RE.sub(repl, body)

    body = body if body.endswith("\n") else body + "\n"
    meta = {"side_files": side_files} if side_files else {}
    return {"title": title, "body": body, "tags": [], "meta": meta, "deferred": deferred}
