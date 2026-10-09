"""路径校验测试: 输入不存在/输入==输出/输出在输入内/空输入."""
import os

from tests._runner import assert_eq, assert_true, cleanup, make_tmp_input, make_tmp_output

from doc2md import pipeline

CASES = []


def test_input_not_exist():
    out = make_tmp_output()
    p, s, e = pipeline.run("/tmp/no_such_dir_zzzz", out)
    assert_eq(e, 1, "应返回 errors=1")
    cleanup(out)


CASES.append(("input_not_exist", test_input_not_exist))


def test_input_eq_output():
    d = make_tmp_input()
    p, s, e = pipeline.run(d, d)
    assert_eq(e, 1)
    cleanup(d)


CASES.append(("input_eq_output", test_input_eq_output))


def test_output_inside_input():
    in_dir = make_tmp_input({"a.txt": "x"})
    out = os.path.join(in_dir, "kn")  # 输出在输入内部
    p, s, e = pipeline.run(in_dir, out)
    assert_eq(e, 1)
    assert_true(not os.path.exists(out), "不应在输入内创建产物")
    cleanup(in_dir)


CASES.append(("output_inside_input", test_output_inside_input))


def test_empty_input_warns_no_error():
    in_dir = make_tmp_input()
    out = make_tmp_output()
    p, s, e = pipeline.run(in_dir, out)
    assert_eq(e, 0)
    assert_eq(p, 0)
    cleanup(in_dir, out)


CASES.append(("empty_input_warns_no_error", test_empty_input_warns_no_error))
