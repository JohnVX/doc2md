"""Excel(xlsx)解析: openpyxl, 每 sheet -> markdown 表格.

通用: data_only 取缓存值; 无缓存值的公式单元格回退显示公式文本(不显示成空);
合并单元格展开填满跨度(zip 解析 sheet XML 取 mergeCell, 不整书载入);
截断超长行(300)/列(40); 跳过空 sheet.
"""
from pathlib import Path

from ._common import defer, md_table

MAX_ROWS = 300
MAX_COLS = 40


def _load_formula_map(path):
    """第二遍流式读取(非 data_only), 收集 (sheet, row, col) -> 公式文本.

    用于 data_only 读出 None 时判断该格是否为"无缓存值的公式".
    公式文本形如 "=A2*2". 失败则返回空 dict(不影响主流程).
    """
    import openpyxl
    result = {}
    try:
        wb_f = openpyxl.load_workbook(path, read_only=True, data_only=False)
        for ws in wb_f.worksheets:
            fm = {}
            for row in ws.iter_rows():
                for c in row:
                    v = c.value
                    if isinstance(v, str) and v.startswith("="):
                        fm[(c.row, c.column)] = v
            if fm:
                result[ws.title] = fm
        wb_f.close()
    except Exception:
        pass
    return result


def _col_to_num(letters):
    """A->1, Z->26, AA->27."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _load_merged_fill(path):
    """直接解析 sheet XML 的 <mergeCell ref>, 构建 {(r,c):(top_r,top_c)} 填充映射.

    不整书对象化(避开非 read_only 全量载入的内存/时间代价), 仅 zip+XML 取合并区.
    失败返回 {}(不影响主流程, 退化成"合并值仅在左上角").
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ref_re = re.compile(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$")
    result = {}
    try:
        z = zipfile.ZipFile(path)
        names = set(z.namelist())
        # 1) workbook.xml: sheet name -> r:id (顺序即 sheet 顺序)
        sheets = []
        for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{MAIN}}}sheet"):
            sheets.append((sh.get("name"), sh.get(f"{{{R}}}id")))
        # 2) rels: r:id -> target(worksheets/sheetN.xml)
        rels = {}
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            rid, tgt = rel.get("Id"), rel.get("Target")
            if rid and tgt:
                rels[rid] = tgt
        # 3) 每个 sheet: 直接正则扫 mergeCell ref(不建 XML 树, 超大 sheet 也近零内存)
        merge_re = re.compile(r'<mergeCell\s+ref="([^"]+)"')
        for name, rid in sheets:
            tgt = rels.get(rid)
            if not tgt:
                continue
            tgt_clean = tgt.lstrip("/")
            if not tgt_clean.startswith("xl/"):
                tgt_clean = "xl/" + tgt_clean
            if tgt_clean not in names:
                continue
            try:
                sheet_xml = z.read(tgt_clean).decode("utf-8", "ignore")
            except Exception:
                continue
            fill = {}
            for ref in merge_re.findall(sheet_xml):
                m = ref_re.match(ref)
                if not m:
                    continue
                c1, r1 = _col_to_num(m.group(1)), int(m.group(2))
                c2, r2 = _col_to_num(m.group(3)), int(m.group(4))
                for r in range(r1, r2 + 1):
                    for c in range(c1, c2 + 1):
                        if (r, c) != (r1, c1):
                            fill[(r, c)] = (r1, c1)
            if fill:
                result[name] = fill
        z.close()
    except Exception:
        pass
    return result


def parse(path, ctx=None):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    title = None
    try:
        title = wb.properties.title
    except Exception:
        pass
    if not title:
        title = Path(path).stem

    formula_map = _load_formula_map(path)
    merged_fill = _load_merged_fill(path)

    out = []
    deferred = []
    for ws in wb.worksheets:
        fm = formula_map.get(ws.title, {})
        mfill = merged_fill.get(ws.title, {})
        tops = set(mfill.values())  # 合并区左上角坐标集合
        top_values = {}  # (r,c) -> value, 流式中记录左上角值供跨度格回填
        rows = []
        ncols = 0
        truncated = False
        formula_count = 0
        for ri, row in enumerate(ws.iter_rows(values_only=True), 1):
            if ri > MAX_ROWS:
                truncated = True
                break
            cells = []
            for ci, v in enumerate(row, 1):
                if v is None:
                    # 1) 无缓存值且是公式 -> 显示公式
                    f = fm.get((ri, ci))
                    if f:
                        cells.append(f)
                        formula_count += 1
                    # 2) 合并单元格的跨度格 -> 回填左上角值
                    elif (ri, ci) in mfill:
                        cells.append(str(top_values.get(mfill[(ri, ci)], "")))
                    else:
                        cells.append("")
                else:
                    cells.append(str(v))
                    if (ri, ci) in tops:
                        top_values[(ri, ci)] = v
            while cells and cells[-1] == "":
                cells.pop()
            if not cells:
                continue
            ncols = max(ncols, len(cells))
            rows.append(cells)
        if not rows:
            continue
        out.append(f"### Sheet: {ws.title}")
        if ncols > MAX_COLS:
            rows = [r[:MAX_COLS] + ["…"] for r in rows]
            ncols = MAX_COLS + 1
        out.append(md_table(rows, ncols))
        if truncated:
            out.append(f"_（行数超过 {MAX_ROWS}，已截断）_")
        if formula_count:
            out.append(defer(deferred, "formula-cell", formula_count,
                             "无缓存值, 已回退显示公式文本, 见原文件复核"))
    wb.close()

    body = "\n\n".join(x for x in out if x) or "<!-- 空工作簿 -->"
    return {"title": title, "body": body, "tags": [], "meta": {}, "deferred": deferred}
