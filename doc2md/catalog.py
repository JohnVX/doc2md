"""catalog.yaml + handoff.yaml 生成.

catalog.yaml — 资料库导航索引 (面向: 业务 agent 定位文档; 每文档含 deferred 上下文).
  导航字段: id/title/category/source/source_type/doc/summary/tags.
  章节结构不重复存储——md body 已含标题 + 长 md 顶部有渲染目录.
handoff.yaml  — stage1→stage2 交接说明 + stage2_pending 工作项 (面向: 仅 stage2 agent).
"""
import copy
import logging
from datetime import datetime, timezone

import yaml
from pathlib import Path

from . import util

log = logging.getLogger("catalog")

_CATALOG_HEADER = (
    "# catalog.yaml — 资料库导航索引\n"
    "# 面向: 业务 agent (按类目/标题/摘要定位文档; 章节结构见各 md 文件目录)\n"
    "# 每文档的 deferred 字段为上下文(该文档有哪些未处理项); 聚合工作单在 index/process/handoff.yaml\n"
    "# stage2 agent 请先读 index/process/handoff.yaml 获取交接说明与工作项\n\n"
)

_HANDOFF_HEADER = (
    "# handoff.yaml — stage1 → stage2 交接说明\n"
    "# 面向: stage2 多模态大模型 agent (业务 agent 不需要读本文件)\n"
    "# 用途: 读此文件即获取全部 — 背景/契约/输出指南 + stage2_pending 具体工作项\n\n"
)

_HANDOFF_STATIC = {
    "producer": {
        "name": "doc2md",
        "stage": 1,
        "description": "确定性资料转换处理器, 将异构资料(txt/md/docx/pptx/xlsx/pdf/png/jpeg)统一为 Markdown 知识库",
    },
    "stage1_done": [
        "格式识别与统一 (全部转 Markdown + YAML front-matter)",
        "文字提取 (正文/表格/有序列表, 各格式)",
        "OCR (rapidocr, 中英文, 可选; 成功的已写入正文)",
        "去噪 (空行/页码/样板)",
        "分类 (配置驱动 taxonomy/mapping/keywords)",
        "章节索引 (md 正文渲染 ## 目录, 锚点深链)",
        "增量处理 (sha256 manifest, 未变跳过)",
    ],
    "stage1_not_done": [
        "图片语义理解 (纯图/图表/SmartArt 的内容描述)",
        "公式渲染 (OMML → 可读公式)",
        "复杂版面精修 (多栏阅读序/修订/批注)",
    ],
    "deferred_contract": {
        "image-ocr": {
            "where": "md body 中 <!-- stage2:image-ocr=N --> 标记; 紧邻上方有 ![title](path) 图片引用, path 指向 original-doc/<doc-id>_assets/ 或 original-doc/<relpath>",
            "source": "跟随标记上方的 ![](path) 引用读取图片文件",
            "action": "多模态理解图片, 提取语义描述或文字",
        },
        "inline-image": {
            "where": "md body 中 <!-- stage2:inline-image=N unit=page --> 标记, 紧随该页文字内容; 所在页由紧邻上方的 <!-- page:M --> 锚点确定, 具体页码亦见该项 locs (如 p3)",
            "source": "original-doc/<relpath> (PDF 原件, 按锚点页号定位图片)",
            "action": "提取图内文字或描述图内容; unit=page 表示本页 N 张图可合并为一次本页图示汇总",
        },
        "scan-page": {
            "where": "md body 中 <!-- stage2:scan-page=N --> 标记; 紧邻上方有 ![扫描页](path) 引用, path 指向 original-doc/<doc-id>_assets/scan*.png",
            "source": "跟随标记上方的 ![](path) 引用读取渲染图",
            "action": "OCR 或多模态理解扫描页内容",
        },
        "formula": {
            "where": "md body 中 <!-- stage2:formula=N --> 标记, 紧跟含公式的段落之后 (原位标记, 位置精准)",
            "source": "original-doc/<relpath> (原件, 含 OMML 公式)",
            "action": "渲染或描述公式",
        },
        "chart": {
            "where": "md body 中 <!-- stage2:chart=N --> 标记, 位于该图/图形所在段落之后 (原位标记)",
            "source": "original-doc/<relpath> (原件, 含图表/SmartArt)",
            "action": "多模态理解图表内容",
        },
        "formula-cell": {
            "where": "md body 中 <!-- stage2:formula-cell=N --> 标记, 位于 sheet 表格之后; 具体 sheet 见该项 locs (如 sheet:销售)",
            "source": "original-doc/<relpath> (xlsx 原件)",
            "action": "复核公式计算结果, 更新已回退显示的公式文本",
        },
        "truncated": {
            "where": "md body 中 <!-- stage2:truncated=N --> 标记, 位于被截断的 sheet 表格之后; 具体 sheet 见该项 locs",
            "source": "original-doc/<relpath> (xlsx 原件)",
            "action": "该 sheet 行列超过上限(行2000/列100)已截断, 需读原件补全或改用结构化提取",
        },
        "encrypted": {
            "where": "md body 中 <!-- stage2:encrypted=1 --> 标记 (整篇占位)",
            "source": "original-doc/<relpath> (加密原件)",
            "action": "解密后提取内容",
        },
        "unsupported": {
            "where": "md body 中 <!-- stage2:unsupported=1 --> 标记 (整篇占位)",
            "source": "original-doc/<relpath> (stage1 不支持的格式原件)",
            "action": "解析原件提取内容",
        },
        "parse-failed": {
            "where": "md body 中 <!-- stage2:parse-failed=1 --> 标记 (整篇占位)",
            "source": "original-doc/<relpath> (stage1 解析失败的原件)",
            "action": "重试解析或用多模态理解",
        },
    },
    "procedure": [
        "从 stage2_pending 取一个工作项 (id + doc + source + deferred)",
        "按 deferred 中的 type 查 deferred_contract, 获取 where(标记与原件定位) / source(原件在哪) / action(该做什么)",
        "打开工作项 doc 路径的 md 文件, 用正则 <!-- stage2:type=N --> 找到该 type 的所有标记 (可能多个, 逐个处理)",
        "对每个标记: 按 where 定位原件 (图片类跟随紧邻上方的 ![](path) 引用; PDF/xlsx 等读 source 原件), 执行 action, 将结果替换该标记。若标记带 unit=page/slide/para, 表示该处 N 项共享一个语义容器——可在容器粒度做一次汇总替换(其余位置标 '见上'), 不必逐项重复同一内容",
        "该 type 全部标记替换后, 四处同步更新: md body(已替换) + front-matter deferred(移除该项) + catalog.yaml 该文档 deferred(移除该项) + handoff.yaml stage2_pending(移除该工作项)",
        "若某项确实无法处理(如无解密能力/无对应解析器): 不要假装完成。在该工作项下加 blocked 对象 {reason, toolNeeded, retryable} 并保留标记与 deferred 字段, 继续处理其他项",
        "重复步骤 1-5 直到 stage2_pending 为空 (或只剩 blocked 项)",
    ],
    "termination": {
        "done": "stage2_pending 为空 (全部处理完毕)",
        "blocked": "存在无法处理的项时停下并上报, 不静默丢弃、不假装完成; blocked 项保留在 stage2_pending 供人工介入",
        "idempotent": "重复运行安全: 已替换的标记不再匹配正则, 不会重复处理",
    },
    "blocked_format": {
        "shape": "blocked: {reason: <str>, toolNeeded: <str|null>, retryable: <bool>}",
        "reason": "自由文本, 说明为何无法处理",
        "toolNeeded": "理论上可解但当前缺的工具/能力 (如 'libreoffice'/'OCR引擎'/'解密口令'); 无则为 null",
        "retryable": "补齐 toolNeeded 后能否自动重试: true=可自动重试, false=需人工介入",
    },
    "marker_fields": "标记可选带 unit / class 修饰: <!-- stage2:type=N [unit=page|slide|para] [class=diagram|chart|photo|screenshot|formula] note -->. unit 表示 N 项共享的语义容器(供聚合处理); class 表示内容类别(免探测). 无该修饰时按普通标记处理",
    "loc_field": "deferred 项可含 locs (定位线索列表, 如 ['p3','sheet:销售']). 标记已在正文原位置时(如 docx 公式紧跟所在段落), 无需 locs; 汇总型标记(PDF 内嵌图/pptx 图表/xlsx 截断)带 locs 指明页号或 sheet 名",
    "output_guide": {
        "md_body": "替换 docs/<category>/<doc-id>.md 中的 <!-- stage2: --> 标记为实际内容",
        "front_matter": "同步更新该 md 的 front-matter deferred 字段 (移除已处理项)",
        "catalog": "同步更新 index/artifact/catalog.yaml 中该文档的 deferred 字段",
        "handoff": "从 index/process/handoff.yaml 的 stage2_pending 中移除已处理项",
        "originals": "原件保留不动 (original-doc/ 是只读兜底)",
        "marker_format": r"所有标记格式为 <!-- stage2:type=count [unit=U] [class=C] note -->, 可用正则 <!-- stage2:([\w-]+)=(\d+)((?:\s+\w+=[\w-]+)*)(?:\s+(.*?))? --> 统一提取 (组: type/count/修饰字段/note)",
    },
    "layout": {
        "catalog": "index/artifact/catalog.yaml",
        "handoff": "index/process/handoff.yaml",
        "manifest": "index/process/.manifest.yaml",
        "docs": "docs/<category>/<doc-id>.md",
        "originals": "original-doc/<relpath>",
        "assets": "original-doc/<doc-id>_assets/",
    },
    "layout_note": "index/ 分两类: artifact/ 为产物索引(下游长期使用, 如 catalog.yaml); process/ 为过程索引(供 stage2 补全的台账, 如 handoff.yaml/.manifest.yaml, 补全完成后可归档或弃用)",
}


def _collect_stage2_pending(entries):
    """从 manifest entries 提取 stage2_pending 工作项列表."""
    pending = []
    for e in entries:
        deferred = e.get("deferred") or []
        if deferred:
            pending.append({
                "id": e["id"],
                "title": e.get("title", ""),
                "doc": e.get("doc_path", ""),
                "source": e.get("source", ""),
                "deferred": [dict(d) for d in deferred],
            })
    return pending


def _check_contract_consistency():
    """断言 DEFER_TYPES 与 deferred_contract 完全一致.

    两处是权威列表 (代码枚举 / 交接契约), 新增类型时若只改一处会导致
    stage2 收到未知 type. 此处启动时校验并告警, 不中断运行.
    """
    from .parsers._common import DEFER_TYPES
    contract = set(_HANDOFF_STATIC["deferred_contract"])
    missing = DEFER_TYPES - contract
    extra = contract - DEFER_TYPES
    if missing:
        log.warning("defer 类型 %s 未在 handoff 契约中声明 (stage2 将无法处理)",
                    sorted(missing))
    if extra:
        log.warning("handoff 契约含未使用的 defer 类型: %s", sorted(extra))


def build_catalog(entries, output_root):
    """entries: manifest 全量值 (list[dict]). 写 index/artifact/catalog.yaml (纯导航 + 每文档 deferred 上下文)."""
    by_cat = {}
    docs = []
    for e in entries:
        cat = e.get("category", "unclassified")
        by_cat.setdefault(cat, []).append(e["id"])
        deferred = e.get("deferred") or []
        doc = {
            "id": e["id"],
            "title": e.get("title", ""),
            "category": cat,
            "source": e.get("source", ""),
            "source_type": e.get("source_type", ""),
            "doc": e.get("doc_path", ""),
            "summary": e.get("summary", ""),
            "tags": e.get("tags", []) or [],
        }
        if deferred:
            doc["deferred"] = deferred
        docs.append(doc)
    data = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "categories": {c: sorted(ids) for c, ids in by_cat.items()},
        "documents": docs,
    }
    path = Path(output_root) / "index" / "artifact" / "catalog.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(path, _CATALOG_HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    log.info("catalog 写入: %s (%d 文档)", path, len(docs))
    return path


def build_handoff(entries, output_root):
    """写 index/process/handoff.yaml — stage1→stage2 交接说明 + stage2_pending 工作项."""
    _check_contract_consistency()
    pending = _collect_stage2_pending(entries)
    data = copy.deepcopy(_HANDOFF_STATIC)
    data["stage2_pending"] = pending
    path = Path(output_root) / "index" / "process" / "handoff.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(path, _HANDOFF_HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    log.info("handoff 写入: %s (%d 待 stage2)", path, len(pending))
    return path
