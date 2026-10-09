"""通用工具: sha256_file / stable_doc_id / slugify / relpath / save_asset / asset_ref /
write_text(LF) / pick_title / is_generic_title / heading_slug."""
import hashlib
import os
import re
from pathlib import Path


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def stable_doc_id(relpath):
    return "doc-" + hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:8]


_SLUG_RE = re.compile(r'[\\/:*?"<>|\s]+')

def slugify(name):
    s = _SLUG_RE.sub("_", Path(name).stem).strip("_")
    return s or "doc"


def relpath(path, base):
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return str(path)


def save_asset(data, ctx, ext, prefix="img"):
    """把字节 data 写到 original-doc/<doc_id>_assets/<NN>_<prefix>.<ext>.

    返回 (dest_basename, 从 docs/<cat>/<id>.md 出发的相对引用路径).
    ctx 需含 output_root 与 doc_id. 用于内嵌图(docx/pptx/pdf 渲染页)提取兜底 + OCR.
    """
    root = ctx.get("output_root") if ctx else None
    doc_id = (ctx or {}).get("doc_id", "doc")
    if not root:
        return None, None
    adir = os.path.join(str(root), "original-doc", f"{doc_id}_assets")
    os.makedirs(adir, exist_ok=True)
    seq = (ctx.get("_asset_seq") or 0) + 1
    ctx["_asset_seq"] = seq
    dest = f"{seq:02d}_{prefix}.{ext.lstrip('.')}"
    with open(os.path.join(adir, dest), "wb") as f:
        f.write(data)
    ref = f"../../original-doc/{doc_id}_assets/{dest}"
    return dest, ref


def asset_ref(ctx, dest):
    """构造从 docs/<cat>/<id>.md 指向 original-doc/<doc_id>_assets/<dest> 的相对引用."""
    doc_id = (ctx or {}).get("doc_id", "doc")
    return f"../../original-doc/{doc_id}_assets/{dest}"


def write_text(path, text, encoding="utf-8"):
    """写文本, 强制 LF 行尾 (跨平台一致: Windows 上也写 \\n 而非 CRLF)."""
    with open(path, "w", encoding=encoding, newline="\n") as f:
        f.write(text)


_GENERIC_TITLES = {
    "", "powerpoint 演示文稿", "演示文稿", "microsoft office powerpoint",
    "presentation", "document", "microsoft office word", "doc", "untitled",
    "powerpoint", "word", "excel", "新文档", "新建文档",
}


def is_generic_title(t):
    """标题是否为通用/占位样板 (Office 默认名/XXX 模板残留)."""
    if not t:
        return True
    s = t.strip()
    if not s:
        return True
    if s.lower() in _GENERIC_TITLES:
        return True
    if "XXX" in s or "xxx" in s:
        return True
    return False


def pick_title(candidates, stem):
    """从候选标题里取第一个非通用的, 否则用文件名主干."""
    for c in candidates:
        if c and not is_generic_title(c):
            return c.strip()
    return stem


def heading_slug(title):
    """markdown 标题锚点 slug: 去标点、小写、空格转 -, 保留 CJK 与字母数字."""
    s = re.sub(r'[^\w\s]', '', title or "")
    s = re.sub(r'\s+', '-', s.strip()).lower()
    return s or "section"

