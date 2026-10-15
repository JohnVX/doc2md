"""通用工具: sha256_file / stable_doc_id / relpath / save_asset /
write_text(LF) / pick_title / is_generic_title / heading_slug."""
import hashlib
import os
import re
from pathlib import Path


def sha256_file(path, chunk=1 << 20):
    """流式计算文件 sha256 十六进制摘要."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def stable_doc_id(relpath):
    """由源文件相对路径哈希生成稳定 doc-id (doc-xxxxxxxx)."""
    return "doc-" + hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:8]


def relpath(path, base):
    """计算 path 相对 base 的路径, 统一用 / 分隔."""
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return str(path)


def save_asset(data, ctx, ext, prefix="img"):
    """把字节 data 写到 original-doc/<doc_id>_assets/<NN>_<prefix>.<ext>.

    返回 (dest_basename, 从 docs/<cat>/<id>.md 出发的相对引用路径).
    ctx 需含 output_root 与 doc_id. 用于内嵌图(docx/pptx/pdf 渲染页)提取兜底 + OCR.

    矢量图 (EMF/WMF/SVG): 先渲染为 PNG 再存 (下游多模态模型读不了矢量);
    渲染失败则原样存并保留原扩展名, 由上层标记 defer.
    """
    root = ctx.get("output_root") if ctx else None
    doc_id = (ctx or {}).get("doc_id", "doc")
    if not root:
        return None, None
    # 矢量图归一化: 所有 parser 提取内嵌图都经此, 故在此统一渲染一次
    ext = (ext or "").lstrip(".")
    if ext in ("emf", "wmf", "svg", "emz", "wmz"):
        from . import vectimg
        png = vectimg.render_vector(data, ext)
        if png is not None:
            # 渲染成功: 存 PNG, 前缀标 vec (供 _post_ocr 推断 class=diagram,
            # 因为渲染后的 PNG 字节已丢失"来自矢量"的信息)
            data, ext, prefix = png, "png", "vec"
    adir = os.path.join(str(root), "original-doc", f"{doc_id}_assets")
    os.makedirs(adir, exist_ok=True)
    seq = (ctx.get("_asset_seq") or 0) + 1
    ctx["_asset_seq"] = seq
    dest = f"{seq:02d}_{prefix}.{ext.lstrip('.')}"
    with open(os.path.join(adir, dest), "wb") as f:
        f.write(data)
    ref = f"../../original-doc/{doc_id}_assets/{dest}"
    return dest, ref


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
    # 仅当标题主体就是 X 占位串(如 "XXX"/"XXXX"/"xxx")时才判通用;
    # 不能因含 "xxx" 子串就丢——"Report XXX-2024"/"保险条款xxx版" 都是真实标题.
    if re.fullmatch(r"[xX]{2,}", s):
        return True
    return False


def _norm_for_match(s):
    """归一化用于子串比对: 去空白与常见标点, 小写."""
    return re.sub(r"[\s\u3000“”\"'\\[\\]（）()《》【】,，。.、:：;；-]", "", s or "").lower()


def title_in_body(title, body, min_len=2):
    """标题是否作为内容出现在正文中 (判断元数据标题是否可信).

    元数据标题不可信时 (如 PPT 导 PDF 时把“幻灯片 1”写进了 PDF title),
    它不会出现在正文里; 而真实的文档标题通常会在正文首部出现.
    用归一化子串匹配; 标题过短 (< min_len) 时无法可靠判断, 返回 True (不否定).
    """
    nt = _norm_for_match(title)
    if len(nt) < min_len:
        return True
    return nt in _norm_for_match(body)


def pick_title(candidates, stem, body=None):
    """从候选标题里取第一个非通用且(可选)在正文中出现的, 否则用文件名主干.

    candidates: 元数据标题候选 (按优先级).
    body: 可选, 文档正文; 提供时额外要求标题出现在正文中 (过滤不可信的
          元数据占位标题, 如“幻灯片 1”), 否则回退到文件名.
    """
    for c in candidates:
        if not c or is_generic_title(c):
            continue
        c = c.strip()
        if body is not None and not title_in_body(c, body):
            continue
        return c
    return stem


def heading_slug(title):
    """markdown 标题锚点 slug: 去标点、小写、空格转 -, 保留 CJK/字母/数字/连字符.

    连字符保留(与主流渲染器一致, 如 'a-b' -> 'a-b' 而非 'ab').
    """
    s = re.sub(r'[^\w\s-]', '', title or "")
    s = re.sub(r'\s+', '-', s.strip()).lower()
    return s or "section"


def heading_slugs(titles):
    """对一串标题生成唯一锚点(第一个用原名, 重复的加 -1/-2...).

    与主流 markdown 渲染器(GitHub)对重复标题的去重规则一致, 避免目录里
    多个链接指向同一锚点.
    """
    seen = {}
    out = []
    for t in titles:
        base = heading_slug(t)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}-{n}")
    return out
