"""去噪 / 抽取式 summary / toc / 章节索引(extract_sections) / front-matter (确定性, 不依赖大模型).

围栏代码块内的行不计入 toc/summary; extract_sections 提取全量标题(level+title), 上限 500 防 OOM.
"""
import re

import yaml


def clean_text(text):
    """折叠多余空行、去行尾空白、去首尾空行."""
    if not text:
        return ""
    out = []
    blank = 0
    for ln in text.splitlines():
        ln = ln.rstrip()
        if ln.strip() == "":
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip("\n")


_PAGE_NO = re.compile(
    r'^\s*(?:第\s*[0-9零一二三四五六七八九十百]+\s*页(?:[/／]\s*共\s*[0-9]+\s*页)?'
    r'|page\s*\d+|-\s*\d+\s*-)\s*$', re.IGNORECASE)


def strip_boilerplate(text):
    """去页码行等明显样板.

    注意: 只删"第N页/page N/- N -"这种明显页码, 不删独占一行的纯数字(可能是正文).
    """
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and _PAGE_NO.match(s) and len(s) < 24:
            continue
        out.append(ln)
    return "\n".join(out)


def _is_fence(line):
    s = line.strip()
    return s.startswith("```") or s.startswith("~~~")


def extract_summary(text, maxlen=200):
    """抽取式摘要: 首个像正文的段. 围栏代码块整段跳过(不被内部空行拆穿)."""
    if not text:
        return ""

    in_fence = False
    para = []

    def emit(p):
        if not p:
            return None
        if all(re.match(r'^\s*(?:[-*+]\s|\d+\.\s)', ln) for ln in p):
            return None  # 整段列表项 -> 跳过
        s = re.sub(r"\s+", " ", " ".join(p))
        return s[:maxlen] + ("…" if len(s) > maxlen else "")

    for ln in text.splitlines():
        if _is_fence(ln):
            in_fence = not in_fence
            r = emit(para)
            if r:
                return r
            para = []
            continue
        if in_fence:
            continue  # 代码块内行一律不参与
        s = ln.strip()
        if not s:
            r = emit(para)
            if r:
                return r
            para = []
            continue
        # 标题/图片/注释/引用 行作为段落分隔, 终结当前 para 但自身不入摘要
        if s.startswith(("#", "![", "<!--", ">")):
            r = emit(para)
            if r:
                return r
            para = []
            continue
        para.append(s)
    r = emit(para)
    return r or ""


_HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*$')


def extract_sections(text, max_sections=500):
    """全量提取正文标题(章节级): [{level, title}]. 围栏代码块内的 # 行不算标题.

    上限 max_sections 早停, 避免对超长/退化文档(如几百万行标题)无界收集致 OOM.
    """
    secs = []
    in_fence = False
    for ln in (text or "").splitlines():
        if _is_fence(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(ln)
        if m:
            t = m.group(2).strip()
            if t:
                secs.append({"level": len(m.group(1)), "title": t})
                if len(secs) >= max_sections:
                    break
    return secs


def extract_toc(text, maxn=10):
    """提取正文标题做目录快照(标题字符串列表, 前 maxn 个)."""
    return [s["title"] for s in extract_sections(text)[:maxn]]


def front_matter(d):
    """生成 YAML front-matter. d 需含 id/title/category/source/source_type/tags/summary.
    deferred 非空时也输出."""
    fm = {k: d.get(k) for k in
          ("id", "title", "category", "source", "source_type", "tags", "summary")
          if d.get(k) is not None}
    if d.get("deferred"):
        fm["deferred"] = d["deferred"]
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip() + "\n---\n\n"
