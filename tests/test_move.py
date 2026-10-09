"""--move 模式测试: 原文件移到 original-doc, 输入被消费空."""
import glob
import os

import yaml

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output)

from doc2md import pipeline

CASES = []


def test_move_mode_consumes_input():
    in_dir = make_tmp_input({"a.txt": "文件A内容\n", "b.txt": "文件B内容\n"})
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out, move=True)
    assert_eq(e, 0)
    assert_eq(len(os.listdir(in_dir)), 0, "move 后输入目录应空")
    assert_true(os.path.exists(os.path.join(out, "original-doc", "a.txt")))
    assert_true(os.path.exists(os.path.join(out, "original-doc", "b.txt")))
    assert_eq(len(find_mds(out)), 2)
    cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml"), encoding="utf-8"))
    assert_eq(len(cat["documents"]), 2)
    cleanup(in_dir, out)


CASES.append(("move_mode_consumes_input", test_move_mode_consumes_input))
