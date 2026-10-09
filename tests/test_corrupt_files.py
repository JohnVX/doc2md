"""损坏文件测试: 各格式损坏不崩, 写占位, 其余文件继续."""
import os

from tests._runner import assert_eq, assert_true, cleanup, find_mds, make_tmp_input, make_tmp_output

from doc2md import pipeline

CASES = []


def _corrupt_zip(name):
    """有效 zip 签名但内容损坏."""
    return (name, b"PK\x03\x04" + b"\x00" * 200)


def test_corrupt_docx_handled():
    """损坏 docx (zip 签名但非法) -> 不崩, 写占位, 其余文件继续."""
    in_dir = make_tmp_input({
        "good.txt": "正常文本\n",
        "bad.docx": _corrupt_zip("bad.docx")[1],
    })
    out = make_tmp_output()
    p, _, e = pipeline.run(in_dir, out)
    assert_true(e >= 0, "不应崩溃")
    mds = find_mds(out)
    assert_eq(len(mds), 2, "两个文件都应有 md (1 成功 1 占位)")
    good = [m for m in mds if "正常文本" in open(m, encoding="utf-8").read()]
    assert_eq(len(good), 1, "good.txt 应正常落库")
    cleanup(in_dir, out)


CASES.append(("corrupt_docx_handled", test_corrupt_docx_handled))


def test_corrupt_xlsx_handled():
    """损坏 xlsx -> 不崩, 写占位."""
    in_dir = make_tmp_input({
        "ok.txt": "fine\n",
        "bad.xlsx": _corrupt_zip("bad.xlsx")[1],
    })
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_true(e >= 0, "不应崩溃")
    assert_eq(len(find_mds(out)), 2)
    cleanup(in_dir, out)


CASES.append(("corrupt_xlsx_handled", test_corrupt_xlsx_handled))


def test_corrupt_pptx_handled():
    """损坏 pptx -> 不崩, 写占位."""
    in_dir = make_tmp_input({
        "ok.txt": "fine\n",
        "bad.pptx": _corrupt_zip("bad.pptx")[1],
    })
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_true(e >= 0, "不应崩溃")
    assert_eq(len(find_mds(out)), 2)
    cleanup(in_dir, out)


CASES.append(("corrupt_pptx_handled", test_corrupt_pptx_handled))


def test_corrupt_image_handled():
    """损坏图片 (png 签名但非法) -> 不崩, defer."""
    in_dir = make_tmp_input({
        "ok.txt": "fine\n",
        "bad.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 200,
    })
    out = make_tmp_output()
    _, _, e = pipeline.run(in_dir, out)
    assert_eq(e, 0, "损坏图片不应计 error (走 defer)")
    mds = find_mds(out)
    assert_eq(len(mds), 2, "两个文件都应有 md")
    cleanup(in_dir, out)


CASES.append(("corrupt_image_handled", test_corrupt_image_handled))
