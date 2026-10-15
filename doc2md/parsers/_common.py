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
    "truncated",     # xlsx 大表超出行列上限(已截断, 需查原件)
    "encrypted",     # 加密文档
    "unsupported",   # stage1 不支持的格式
    "parse-failed",  # 解析失败
})


def guess_image_class(blob=b"", ext="", filter_name=""):
    """从图片字节/扩展名/PDF filter 弱推断内容类别 (diagram/chart/photo/screenshot).

    诚实说明: 仅凭字节无法可靠区分图表与示意图, 故只用有区分度的信号;
    无法判断时返回空串 (标记不输出 class, 下游自行探测). 不编造类别。

    ext 可以是裸扩展名 ("png") 或文件名 ("01_img.png"); 内部取最后一个点后的段.

    信号:
      ext/filter 为 JPEG(DCTDecode/jpg) -> 多为照片 (photo)
      矢量格式 (svg/emf/wmf)            -> 图示 (diagram)
      PNG/GIF 等无损位图                  -> 截图/图表候选 (screenshot)
    """
    e = (ext or "").lower().strip()
    if "." in e:
        e = e.rsplit(".", 1)[1]
    # 文件名含 vec 前缀: 由矢量图渲染而来 (save_asset 标记), 归为 diagram
    if "vec" in (ext or "").lower().split(".")[0].split("_"):
        return "diagram"
    f = (filter_name or "").lower()
    head = blob[:12] if blob else b""
    if e in ("svg", "emf", "wmf") or head[:4] == b"\x01\x00\x00\x00":
        return "diagram"
    if e in ("jpg", "jpeg") or f in ("dctdecode", "jpxdecode") or head[:3] == b"\xff\xd8\xff":
        return "photo"
    if e in ("png", "gif", "bmp", "tif", "tiff", "webp"):
        # 无损位图: 截图/图表/示意图均可能, 给最保守的 screenshot
        return "screenshot"
    return ""


def defer(deferred, type, count=1, note="", loc="", cls="", unit=""):
    """记录结构化 deferred 项并返回标准化 stage2 标记串.

    标记格式: <!-- stage2:{type}={count} [unit={unit}] [class={cls}] {note} -->
    可统一提取: re.findall(r'<!-- stage2:([\w-]+)=(\d+)((?:\s+\w+=[\w-]+)*)(?:\s+(.*?))? -->', body)

    语义约定 (关键, 避免下游误算工作量):
      count —— 恒为"待办项数"(需 stage2 处理的条目数), 不是批次编号/容器大小.
      unit  —— 可选"共享容器"类型(page/slide/section). 当同一容器内多项共享一次
               语义替换时标注 (如 PDF 一页 8 张图 -> count=8 unit=page). 下游可据此
               把 count 项"聚合为一次处理", 避免逐项重复填同页内容.
      cls   —— 可选"内容类别"(diagram/chart/photo/screenshot/formula), 让 stage2
               免探测即知该图该用什么方式处理.

    deferred: list, 调用方传入, 本函数 append 一项 {type, count, note, loc, cls, unit}.
    loc: 可选定位线索 (如 PDF 页号 'p3' / xlsx 'sheet:销售' / docx 段落序),
         供 stage2 在文件中定位该项; 不体现在正文标记里, 仅用于 front-matter/catalog/handoff.
    返回: 标准化标记字符串, 拼入 body 即可.
    """
    note = (note or "").replace("-->", "").replace("\n", " ").strip()
    item = {"type": type, "count": count, "note": note}
    if loc:
        item["loc"] = loc
    if cls:
        item["cls"] = cls
    if unit:
        item["unit"] = unit
    deferred.append(item)
    tag = f"<!-- stage2:{type}={count}"
    if unit:
        tag += f" unit={unit}"
    if cls:
        tag += f" class={cls}"
    if note:
        tag += f" {note}"
    return tag + " -->"


def aggregate_deferred(items):
    """合并同 type 的 deferred 项: count 求和, note 取首个非空, loc 汇总为列表.

    locs 保留全部定位线索 (去重, 按出现序) —— 同类型多项分布在不同位置时不丢位置.
    仅当某项有 loc 时输出 locs 字段, 不产生空字段噪声.
    cls/unit 同 type 下应一致 (由 parser 保证), 取首次出现的值.
    """
    by_type = {}
    for it in items:
        t = it["type"]
        if t in by_type:
            by_type[t]["count"] += it["count"]
            if not by_type[t]["note"] and it.get("note"):
                by_type[t]["note"] = it["note"]
            if not by_type[t].get("cls") and it.get("cls"):
                by_type[t]["cls"] = it["cls"]
            if not by_type[t].get("unit") and it.get("unit"):
                by_type[t]["unit"] = it["unit"]
        else:
            by_type[t] = {"type": t, "count": it["count"], "note": it.get("note", "")}
            if it.get("cls"):
                by_type[t]["cls"] = it["cls"]
            if it.get("unit"):
                by_type[t]["unit"] = it["unit"]
            if it.get("loc"):
                by_type[t]["_locs"] = []
        loc = it.get("loc")
        if loc:
            by_type[t].setdefault("_locs", [])
            if loc not in by_type[t]["_locs"]:
                by_type[t]["_locs"].append(loc)
    out = []
    for t, d in by_type.items():
        locs = d.pop("_locs", None)
        if locs:
            d["locs"] = locs
        out.append(d)
    return out

# 母版/占位符样板文本 (中英). 用"整串匹配"而非全文子串:
# 占位符是 Powerpoint 母版里的**固定短样板句**(如 "Click to add title"),
# 真实正文(如 "Set the title style to bold"/"Click here to add a note")含关键词但非样板,
# 不应误删. 否则违反"不丢内容"原则. 故只枚举固定词汇的整串形态.
_PLACEHOLDER_EXACT = re.compile(
    r"^(?:单击此处添加|点击此处添加|单击添加|占位符)(?:标题|文本|内容|副标题|图片)?$",
    re.IGNORECASE)
_PLACEHOLDER_EN = re.compile(
    r"^(?:click\s+to\s+(?:add|edit)\s+(?:text|title|subtitle|content|picture|image)"
    r"|placeholder\s*text\s*\d*"
    r"|title\s*\d*)$",
    re.IGNORECASE)


def is_placeholder(text):
    """检测是否为母版占位符文本 (整串固定样板, 不误删含关键词的真实正文)."""
    s = text.strip()
    if not s:
        return True
    return bool(_PLACEHOLDER_EXACT.match(s) or _PLACEHOLDER_EN.match(s))


def md_table(rows, ncols=None):
    """rows: list[list[str]]. 首行作表头. 返回 markdown 表格字符串."""
    if not rows:
        return ""
    if ncols is None:
        ncols = max(len(r) for r in rows)
    if ncols < 1:
        return ""

    def esc(c):
        """转义表格单元格特殊字符.

        注意: 除 \\n 外还需处理 \\x0b(垂直制表符)/\\x0c(换页)等 —— PowerPoint/
        Word 单元格内换行用 \\x0b(python-pptx 的 cell.text 会返回它), 不处理会
        导致 markdown 表格行被拆断.
        """
        s = str(c)
        for ch in ("\n", "\x0b", "\x0c", "\r"):
            s = s.replace(ch, " ")
        return s.replace("\\", "\\\\").replace("|", "\\|").strip()

    def pad(r):
        """补齐行到 ncols 列."""
        r = list(r) + [""] * (ncols - len(r))
        return [esc(c) for c in r[:ncols]]

    lines = ["| " + " | ".join(pad(rows[0])) + " |"]
    lines.append("|" + "|".join(["---"] * ncols) + "|")
    for r in rows[1:]:
        lines.append("| " + " | ".join(pad(r)) + " |")
    return "\n".join(lines)


def render_table(tbl):
    """渲染 docx/pptx 表格对象为 md 表格 (两处实现完全一致, 统一于此).

    不在此转义: 提取原始文本, 转义统一由 md_table 的 esc 处理
    (避免 | 被双重转义 => \\|).
    """
    try:
        ncols = len(tbl.columns)
    except Exception:
        ncols = max((len(r.cells) for r in tbl.rows), default=0)
    if ncols < 1 or not tbl.rows:
        return ""
    rows = []
    for r in tbl.rows:
        rows.append([c.text for c in r.cells])
    return md_table(rows, ncols)
