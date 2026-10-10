"""并行处理测试: 确定性 (多次运行产物一致) + 多文件顺序不受线程影响."""
import os
import yaml

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output)
from tests import _samples

from doc2md import pipeline

CASES = []


def test_parallel_determinism():
    """并行处理多文件: 两次运行 catalog 文档顺序和内容完全一致."""
    corpus = {
        "a.txt": "文件A内容\n",
        "b.txt": "文件B内容\n",
        "c.md": "# 标题C\n\n正文C\n",
        "d.txt": "D内容\n",
        "e.md": "# 标题E\n\n正文E\n",
    }
    in1 = make_tmp_input(corpus)
    out1 = make_tmp_output()
    pipeline.run(in1, out1)
    cat1 = yaml.safe_load(open(os.path.join(out1, "index", "catalog.yaml"), encoding="utf-8"))
    docs1 = [(d["id"], d["title"]) for d in cat1["documents"]]

    in2 = make_tmp_input(corpus)
    out2 = make_tmp_output()
    pipeline.run(in2, out2)
    cat2 = yaml.safe_load(open(os.path.join(out2, "index", "catalog.yaml"), encoding="utf-8"))
    docs2 = [(d["id"], d["title"]) for d in cat2["documents"]]

    assert_eq(len(docs1), len(docs2), "文档数应一致")
    for i, (d1, d2) in enumerate(zip(docs1, docs2)):
        assert_eq(d1[0], d2[0], f"doc-id 应一致 (位置 {i})")
        assert_eq(d1[1], d2[1], f"title 应一致 (位置 {i})")
    cleanup(in1, out1)
    cleanup(in2, out2)


CASES.append(("parallel_determinism", test_parallel_determinism))


def test_parallel_many_files_ordered():
    """并行处理 10 个文件: catalog 文档顺序按文件名排序, 不受线程完成顺序影响."""
    corpus = {}
    for i in range(10):
        corpus[f"file_{i:02d}.txt"] = f"内容 {i}\n"
    in_dir = make_tmp_input(corpus)
    out = make_tmp_output()
    pipeline.run(in_dir, out)
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml"), encoding="utf-8"))
    titles = [d["title"] for d in cat["documents"]]
    expected = [f"file_{i:02d}" for i in range(10)]
    assert_eq(titles, expected, "文档顺序应按文件名排序")
    cleanup(in_dir, out)


CASES.append(("parallel_many_files_ordered", test_parallel_many_files_ordered))


def test_parallel_mixed_formats_correct():
    """并行处理混合格式: 全部正确处理, 无遗漏."""
    in_dir = make_tmp_input()
    expected = _samples.generate_corpus(in_dir)
    out = make_tmp_output()
    p, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0, "不应有失败")
    assert_eq(p, len(expected), "全部样本应被处理")
    assert_eq(len(find_mds(out)), len(expected), "md 数应等于输入数")
    for fn in expected:
        assert_true(os.path.exists(os.path.join(out, "original-doc", fn)), f"{fn} 未拷贝")
    cleanup(in_dir, out)


CASES.append(("parallel_mixed_formats_correct", test_parallel_mixed_formats_correct))
