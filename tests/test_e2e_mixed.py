"""端到端多格式混合 + 原件保留 + 带分类配置."""
import os
import yaml

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output)

from doc2md import pipeline

CASES = []


def test_multi_format_mixed():
    """多种格式混合输入(docx+xlsx+pptx+md+txt) -> 全部处理, catalog 完整."""
    from tests import _samples
    import tempfile
    tmp = tempfile.mkdtemp()
    docx_p = os.path.join(tmp, "doc.docx")
    xlsx_p = os.path.join(tmp, "tab.xlsx")
    pptx_p = os.path.join(tmp, "slides.pptx")
    _samples.gen_docx(docx_p)
    _samples.gen_xlsx(xlsx_p)
    _samples.gen_pptx(pptx_p)
    try:
        in_dir = make_tmp_input({
            "doc.docx": open(docx_p, "rb").read(),
            "tab.xlsx": open(xlsx_p, "rb").read(),
            "slides.pptx": open(pptx_p, "rb").read(),
            "readme.md": "# Readme\n正文\n",
            "notes.txt": "纯文本笔记\n",
        })
        out = make_tmp_output()
        p, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0, "全部格式应成功")
        assert_eq(p, 5, "5 个文件都应处理")
        mds = find_mds(out)
        assert_eq(len(mds), 5)
        cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
        types = sorted(d["source_type"] for d in cat["documents"])
        assert_eq(types, ["docx", "md", "pptx", "txt", "xlsx"],
                  f"应有 5 种 source_type, got {types}")
        cleanup(in_dir, out)
    finally:
        cleanup(tmp)


CASES.append(("multi_format_mixed", test_multi_format_mixed))


def test_original_doc_preserved():
    """原件复制到 original-doc/, 路径与 front-matter source 一致."""
    in_dir = make_tmp_input({"report.txt": "important content\n"})
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    mds = find_mds(out)
    body = open(mds[0], encoding="utf-8").read()
    from tests._runner import parse_front_matter
    fm = parse_front_matter(body)
    source = fm["source"]
    # source 是相对路径, 检查文件存在
    src_path = os.path.join(out, source)
    assert_true(os.path.isfile(src_path), f"原件应存在于 {src_path}")
    content = open(src_path).read()
    assert_true("important content" in content, "原件内容应不变")
    cleanup(in_dir, out)


CASES.append(("original_doc_preserved", test_original_doc_preserved))


def test_e2e_with_config():
    """带分类配置端到端 -> 文档按 mapping/keywords 分类到不同类目."""
    in_dir = make_tmp_input({
        "attack_report.txt": "漏洞 exploit 攻击面分析\n",
        "design_doc.txt": "架构 内核 设计 内存管理\n",
        "random.txt": "无关键词的普通文档\n",
    })
    cfg = os.path.join(make_tmp_output(), "config.yaml")
    with open(cfg, "w") as f:
        f.write("taxonomy: [attack-surface, architecture, misc]\n"
                "default_category: misc\n"
                "mapping:\n"
                '  "attack_report.txt": attack-surface\n'
                "keywords:\n"
                "  attack-surface: [漏洞, exploit, 攻击面]\n"
                "  architecture: [架构, 内核, 设计]\n")
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out, cfg)
    assert_eq(e, 0)
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    by_id = {d["id"]: d for d in cat["documents"]}
    cats = {d["title"]: d["category"] for d in cat["documents"]}
    assert_eq(cats.get("attack_report"), "attack-surface", "mapping 应优先")
    assert_eq(cats.get("design_doc"), "architecture", "keywords 应命中")
    assert_eq(cats.get("random"), "misc", "无命中应落 default")
    cleanup(in_dir, out, os.path.dirname(cfg))


CASES.append(("e2e_with_config", test_e2e_with_config))
