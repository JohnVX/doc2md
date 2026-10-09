"""识别器测试: 各格式样本 + 扩展名改错诱饵 (全靠代码生成)."""
import os
import tempfile

from tests import _samples
from tests._runner import assert_eq, assert_true, cleanup, make_tmp_input

from doc2md import detector

CASES = []


def test_detect_corpus():
    tmp = make_tmp_input()
    expected = _samples.generate_corpus(tmp)
    for fn, exp in expected.items():
        r = detector.detect(os.path.join(tmp, fn))
        assert_eq(r["format"], exp, f"{fn}")
    cleanup(tmp)


CASES.append(("detect_corpus", test_detect_corpus))


def test_detect_decoys_content_based():
    """扩展名故意改错, 仍应靠内容签名识别正确."""
    tmp = make_tmp_input()
    _samples.generate_corpus(tmp)
    # (原文件, 伪装扩展名, 期望 format)
    plan = [
        ("sample.docx", ".dat", "docx"),
        ("data.xlsx", ".docx", "xlsx"),
        ("slides.pptx", ".txt", "pptx"),
        ("doc.pdf", ".png", "pdf"),
        ("pic.png", ".jpg", "png"),
        ("CMakeLists.txt", ".md", "code"),
        ("readme.md", ".txt", "md"),
    ]
    for src, fake, exp in plan:
        src_path = os.path.join(tmp, src)
        stem = os.path.splitext(src)[0]
        dst = os.path.join(tmp, stem + fake)
        os.rename(src_path, dst)
        r = detector.detect(dst)
        assert_eq(r["format"], exp, f"{src}伪装{fake}")
    cleanup(tmp)


CASES.append(("detect_decoys", test_detect_decoys_content_based))


def test_large_chinese_text_encoding():
    """>64KB 的 utf-8 中文文本不应被截断误判成 latin-1/gbk."""
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "big_zh.txt")
    text = "鸿蒙内核内存管理测试文本。" * 6000  # ~216KB
    assert len(text.encode("utf-8")) > 65536
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    r = detector.detect(p)
    assert_eq(r["meta"].get("encoding"), "utf-8", "大中文文本应嗅探为 utf-8")
    sample = open(p, "r", encoding=r["meta"]["encoding"]).read()[:20]
    assert_true(sample.startswith("鸿蒙内核内存管理测试文本"), "解码应正确不乱码")
    cleanup(tmp)


CASES.append(("large_chinese_text_encoding", test_large_chinese_text_encoding))


def test_gbk_text_encoding():
    """gbk 编码的中文文本应嗅探为 gbk, 不乱码."""
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "gbk.txt")
    text = "鸿蒙内核内存管理测试。"
    with open(p, "w", encoding="gbk") as f:
        f.write(text)
    r = detector.detect(p)
    assert_eq(r["meta"].get("encoding"), "gbk", "gbk 文件应嗅探为 gbk")
    assert_eq(open(p, "r", encoding="gbk").read(), text, "解码应正确")
    cleanup(tmp)


CASES.append(("gbk_text_encoding", test_gbk_text_encoding))
