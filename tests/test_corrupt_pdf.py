"""损坏 PDF 测试: 解析失败但不崩, 写占位 md, 其余文件继续."""
from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output)

from doc2md import pipeline

CASES = []


def test_corrupt_pdf_handled():
    in_dir = make_tmp_input({
        "good.txt": "正常文本\n",
        "bad.pdf": b"%PDF-1.4\nbroken garbage not a real pdf\n%%EOF\n",
    })
    out = make_tmp_output()
    p, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 1, "损坏pdf应计1失败")
    assert_eq(p, 2, "两个文件都被处理(1成功1失败占位)")
    contents = [(m, open(m, encoding="utf-8").read()) for m in find_mds(out)]
    stubs = [m for m, t in contents if "parse-failed" in t]
    assert_eq(len(stubs), 1, "应有一个含 stage2:parse-failed 的占位md")
    good = [m for m, t in contents if "正常文本" in t]
    assert_eq(len(good), 1, "good.txt 应正常落库")
    cleanup(in_dir, out)


CASES.append(("corrupt_pdf_handled", test_corrupt_pdf_handled))
