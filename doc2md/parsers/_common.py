"""解析器共享小工具: md 表格 / 占位符检测 / stage2 defer 结构化标记."""
import re

# stage2 defer 类型枚举 (parser 只用这些, 保证可编程统一处理)
DEFER_TYPES = frozenset({
    "image-ocr",     # 图片无文字/无引擎, 需多模态理解
    "inline-image",  # PDF 文字页内嵌图, 图内文字未提取
    "scan-page",     # 扫描页渲染/OCR 失败
    "formula",       # 数学公式(OMML)未提取
    "chart",         # 图表/SmartArt 未提取
    "formula-cell",  # xlsx 公式单元格无缓存值(已回退显示公式文本, 需复核)
    "encrypted",     # 加密文档
    "unsupported",   # stage1 不支持的格式
    "parse-failed",  # 解析失败
})


def defer(deferred, type, count=1, note=""):
    """记录结构化 deferred 项并返回标准化 stage2 标记串.

    标记格式: <!-- stage2:{type}={count} {note} -->
    可用正则统一提取: re.findall(r'<!-- stage2:([\\w-]+)=(\\d+)(?:\\s+(.*?))? -->', body)

    deferred: list, 调用方传入, 本函数 append 一项 {type, count, note}.
    返回: 标准化标记字符串, 拼入 body 即可.
    """
    note = (note or "").replace("-->", "").replace("\n", " ").strip()
    deferred.append({"type": type, "count": count, "note": note})
    tag = f"<!-- stage2:{type}={count}"
    if note:
        tag += f" {note}"
    return tag + " -->"


def aggregate_deferred(items):
    """合并同 type 的 deferred 项: count 求和, note 取首个非空."""
    by_type = {}
    for it in items:
        t = it["type"]
        if t in by_type:
            by_type[t]["count"] += it["count"]
        else:
            by_type[t] = {"type": t, "count": it["count"], "note": it.get("note", "")}
    return list(by_type.values())

# 母版/占位符样板文本 (中英), 命中则跳过
_PLACEHOLDER = re.compile(
    r"(单击此处添加|点击此处添加|单击添加|占位符|"
    r"click\s+to\s+add|click\s+to\s+edit|placeholder\s*text|title\s*\d*)",
    re.IGNORECASE)


def is_placeholder(text):
    """检测是否为母版占位符文本."""
    s = text.strip().lower()
    if not s:
        return True
    return bool(_PLACEHOLDER.search(s))


def md_table(rows, ncols=None):
    """rows: list[list[str]]. 首行作表头. 返回 markdown 表格字符串."""
    if not rows:
        return ""
    if ncols is None:
        ncols = max(len(r) for r in rows)
    if ncols < 1:
        return ""

    def esc(c):
        """转义表格单元格特殊字符."""
        return str(c).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()

    def pad(r):
        """补齐行到 ncols 列."""
        r = list(r) + [""] * (ncols - len(r))
        return [esc(c) for c in r[:ncols]]

    lines = ["| " + " | ".join(pad(rows[0])) + " |"]
    lines.append("|" + "|".join(["---"] * ncols) + "|")
    for r in rows[1:]:
        lines.append("| " + " | ".join(pad(r)) + " |")
    return "\n".join(lines)
