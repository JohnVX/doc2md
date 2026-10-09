"""端到端冒烟: 生成全套样本 -> 跑 pipeline -> 校验产物完整."""
import os

import yaml

from tests import _samples
from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output, parse_front_matter)

from doc2md import pipeline

CASES = []


def test_e2e_smoke():
    in_dir = make_tmp_input()
    expected = _samples.generate_corpus(in_dir)
    out = make_tmp_output()
    p, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0, "不应有失败")
    assert_eq(p, len(expected), "全部样本应被处理")
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml"), encoding="utf-8"))
    assert_eq(len(cat["documents"]), len(expected))
    for fn in expected:
        assert_true(os.path.exists(os.path.join(out, "original-doc", fn)), f"{fn} 未拷贝")
    assert_eq(len(find_mds(out)), len(expected))
    cleanup(in_dir, out)


CASES.append(("e2e_smoke", test_e2e_smoke))


def test_incremental_stable_ids():
    """重跑后 doc-id 稳定 (同源文件 -> 同 id)."""
    in_dir = make_tmp_input({"x.txt": "内容\n"})
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    man = yaml.safe_load(open(os.path.join(out, "index", ".manifest.yaml"), encoding="utf-8"))
    id1 = man["entries"][0]["id"]
    pipeline.run(in_dir, out)  # 重跑(跳过)
    man = yaml.safe_load(open(os.path.join(out, "index", ".manifest.yaml"), encoding="utf-8"))
    id2 = man["entries"][0]["id"]
    assert_eq(id1, id2, "doc-id 应稳定")
    cleanup(in_dir, out)


CASES.append(("incremental_stable_ids", test_incremental_stable_ids))


def test_front_matter_valid_and_consistent():
    """生成的 md front-matter 须是合法 YAML, 字段与 catalog 一致."""
    in_dir = make_tmp_input({"note.txt": "一段内容用于校验 front-matter\n"})
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml"), encoding="utf-8"))
    doc = cat["documents"][0]
    fm = parse_front_matter(open(os.path.join(out, doc["doc"]), encoding="utf-8").read())
    for k in ("id", "title", "category", "source", "source_type"):
        assert_true(k in fm, f"front-matter 缺字段 {k}")
        assert_eq(fm[k], doc[k], f"front-matter {k} 与 catalog 不一致")
    cleanup(in_dir, out)


CASES.append(("front_matter_valid_and_consistent", test_front_matter_valid_and_consistent))


def test_catalog_has_sections_with_anchors():
    """catalog 每文档应有 sections(全量标题, 带 level/anchor); 有标题的文档 sections 非空."""
    body = "# 概述\n\n正文\n\n## 章节一\n\na\n\n## 章节二\n\nb\n\n## 章节三\n\nc\n\n## 章节四\n\nd\n\n## 章节五\n\ne\n"
    in_dir = make_tmp_input({"note.md": body})
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml"), encoding="utf-8"))
    doc = cat["documents"][0]
    secs = doc.get("sections", [])
    assert_true(len(secs) >= 6, f"应有 >=6 sections, 实得 {len(secs)}")
    s0 = secs[0]
    assert_true({"level", "title", "anchor"} <= set(s0.keys()), f"section 缺字段: {s0}")
    assert_eq(s0["title"], "概述")
    assert_true(s0["anchor"], "anchor 非空")
    # >=6 标题的文档应有顶部目录
    txt = open(os.path.join(out, doc["doc"]), encoding="utf-8").read()
    assert_true("## 目录" in txt, ">=6 标题的文档应有顶部目录")
    cleanup(in_dir, out)


CASES.append(("catalog_has_sections_with_anchors", test_catalog_has_sections_with_anchors))
