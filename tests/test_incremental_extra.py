"""增量多次使用场景: 新增文件, 3次以上, 删除+恢复, 改config."""
import os
import yaml

from tests._runner import assert_eq, assert_true, cleanup, find_mds, make_tmp_input, make_tmp_output

from doc2md import pipeline

CASES = []


def test_new_file_added():
    """第一次跑 2 个文件, 加 1 个新文件再跑 -> 只处理新增的."""
    in_dir = make_tmp_input({"a.txt": "aaa\n", "b.txt": "bbb\n"})
    out = make_tmp_output()
    p1, s1, _ = pipeline.run(in_dir, out)
    assert_eq(p1, 2)
    # 加新文件
    with open(os.path.join(in_dir, "c.txt"), "w") as f:
        f.write("ccc\n")
    p2, s2, _ = pipeline.run(in_dir, out)
    assert_eq(p2, 1, "只处理新增的 c.txt")
    assert_eq(s2, 2, "a/b 应跳过")
    assert_eq(len(find_mds(out)), 3)
    cleanup(in_dir, out)


CASES.append(("new_file_added", test_new_file_added))


def test_three_runs():
    """3 次增量: 跑 -> 全跳过 -> 加文件 -> 只处理新增."""
    in_dir = make_tmp_input({"x.txt": "init\n"})
    out = make_tmp_output()
    # 第1次: 全处理
    p1, s1, _ = pipeline.run(in_dir, out)
    assert_eq(p1, 1)
    assert_eq(s1, 0)
    # 第2次: 全跳过
    p2, s2, _ = pipeline.run(in_dir, out)
    assert_eq(p2, 0)
    assert_eq(s2, 1)
    # 第3次: 加新文件
    with open(os.path.join(in_dir, "y.txt"), "w") as f:
        f.write("new\n")
    p3, s3, _ = pipeline.run(in_dir, out)
    assert_eq(p3, 1, "处理新增 y.txt")
    assert_eq(s3, 1, "x.txt 跳过")
    cleanup(in_dir, out)


CASES.append(("three_runs", test_three_runs))


def test_delete_then_readd_docid_stable():
    """删文件再加回同名同内容 -> doc-id 不变 (跨删除恢复稳定)."""
    in_dir = make_tmp_input({"doc.txt": "content\n"})
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    cat1 = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    id1 = cat1["documents"][0]["id"]
    # 删文件
    os.remove(os.path.join(in_dir, "doc.txt"))
    pipeline.run(in_dir, out)
    cat2 = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    assert_eq(len(cat2["documents"]), 0, "删除后应无文档")
    # 加回同名同内容
    with open(os.path.join(in_dir, "doc.txt"), "w") as f:
        f.write("content\n")
    pipeline.run(in_dir, out)
    cat3 = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    id3 = cat3["documents"][0]["id"]
    assert_eq(id1, id3, f"doc-id 应稳定: {id1} == {id3}")
    cleanup(in_dir, out)


CASES.append(("delete_then_readd_docid_stable", test_delete_then_readd_docid_stable))


def test_config_change_reclassifies():
    """改 config 后重跑 -> 类目变化, 旧类目 md 清理."""
    in_dir = make_tmp_input({"report.txt": "漏洞 exploit 攻击面\n"})
    out = make_tmp_output()
    # 第一次: 分类为 attack
    cfg1 = os.path.join(out, "_cfg1.yaml")
    with open(cfg1, "w") as f:
        f.write("taxonomy: [attack, safe]\ndefault_category: safe\n"
                "keywords:\n  attack: [漏洞, exploit]\n")
    pipeline.run(in_dir, out, cfg1)
    cat1 = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    assert_eq(cat1["documents"][0]["category"], "attack")
    # 第二次: 改 config, 同文件归 safe (关键词变)
    cfg2 = os.path.join(out, "_cfg2.yaml")
    with open(cfg2, "w") as f:
        f.write("taxonomy: [attack, safe]\ndefault_category: safe\n"
                "keywords:\n  safe: [漏洞]\n")  # 漏洞现在归 safe
    pipeline.run(in_dir, out, cfg2)
    cat2 = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    assert_eq(cat2["documents"][0]["category"], "safe", "改 config 后应重新分类")
    # 旧类目目录应无残留 md
    old_md = os.path.join(out, "docs", "attack")
    if os.path.isdir(old_md):
        assert_eq(len(os.listdir(old_md)), 0, "旧类目目录应空")
    cleanup(in_dir, out)


CASES.append(("config_change_reclassifies", test_config_change_reclassifies))


def test_simultaneous_add_delete_modify():
    """同时: 加新文件 + 删旧文件 + 改一个文件 -> 各自正确处理."""
    in_dir = make_tmp_input({"keep.txt": "keep\n", "del.txt": "delete me\n", "mod.txt": "original\n"})
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    # 删 del.txt, 改 mod.txt, 加 new.txt
    os.remove(os.path.join(in_dir, "del.txt"))
    with open(os.path.join(in_dir, "mod.txt"), "w") as f:
        f.write("modified\n")
    with open(os.path.join(in_dir, "new.txt"), "w") as f:
        f.write("new file\n")
    p, s, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    assert_eq(len(find_mds(out)), 3, "应剩 3 个 md (keep+mod+new)")
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
    titles = [d["title"] for d in cat["documents"]]
    assert_true("del" not in str(titles).lower() or "delete" not in str(titles).lower(),
                "del.txt 应被清理")
    cleanup(in_dir, out)


CASES.append(("simultaneous_add_delete_modify", test_simultaneous_add_delete_modify))
