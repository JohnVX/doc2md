"""纯文本 / 代码解析: 通用. 代码裹带语言标注的围栏; 纯文本直出."""
from pathlib import Path


def parse(path, ctx=None):
    ctx = ctx or {}
    enc = ctx.get("encoding") or "utf-8"
    text = Path(path).read_text(encoding=enc, errors="replace")
    lang = ctx.get("lang") or "text"
    if lang and lang not in ("text", "plain"):
        body = "```" + lang + "\n" + text.rstrip() + "\n```\n"
    else:
        body = text if text.endswith("\n") else text + "\n"
    return {"title": Path(path).stem, "body": body, "tags": [], "meta": {"lang": lang}}
