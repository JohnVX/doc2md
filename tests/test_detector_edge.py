"""识别器边界测试: 空文件 / OLE2 旧 office / 随机二进制, 均应 handler=None 跳过或 defer."""
import os

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output)

from doc2md import detector, pipeline

CASES = []


def test_empty_file():
    tmp = make_tmp_input({"empty.txt": "", "empty.md": ""})
    for fn in ("empty.txt", "empty.md"):
        r = detector.detect(os.path.join(tmp, fn))
        assert_eq(r["format"], "empty", fn)
        assert_true(r["handler"] is None, fn)
    cleanup(tmp)


CASES.append(("empty_file", test_empty_file))


def test_ole2_header():
    tmp = make_tmp_input({"old.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1garbage"})
    r = detector.detect(os.path.join(tmp, "old.doc"))
    assert_eq(r["format"], "ole2")
    assert_true(r["handler"] is None)
    cleanup(tmp)


CASES.append(("ole2_header", test_ole2_header))


def test_random_binary_has_null():
    tmp = make_tmp_input({"rand.bin": b"\x00" + os.urandom(2047)})  # 确定性含 null
    r = detector.detect(os.path.join(tmp, "rand.bin"))
    assert_eq(r["format"], "binary")
    assert_true(r["handler"] is None)
    cleanup(tmp)


CASES.append(("random_binary", test_random_binary_has_null))


def test_pipeline_skips_empty_defers_others():
    """空文件跳过; 非空不支持格式(ole2/binary) defer 进知识库(原件+md)."""
    in_dir = make_tmp_input({
        "good.txt": "正常文本\n",
        "empty.txt": "",
        "old.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1garbage",
        "rand.bin": b"\x00" + os.urandom(511),  # 确定性含 null
    })
    out = make_tmp_output()
    p, _, e = pipeline.run(in_dir, out)
    # good.txt 处理 + old.doc/rand.bin defer = 3 个 md; 空文件跳过
    assert_eq(p, 3)
    assert_eq(e, 0)
    mds = find_mds(out)
    assert_eq(len(mds), 3)
    defer_mds = [m for m in mds if "stage2" in open(m, encoding="utf-8").read()]
    assert_eq(len(defer_mds), 2, "ole2/binary 两个应 defer")
    assert_true(os.path.exists(os.path.join(out, "original-doc", "old.doc")))
    assert_true(os.path.exists(os.path.join(out, "original-doc", "rand.bin")))
    cleanup(in_dir, out)


CASES.append(("pipeline_skips_empty_defers_others", test_pipeline_skips_empty_defers_others))
