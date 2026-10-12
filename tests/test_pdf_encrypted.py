"""加密 PDF → encrypted defer — 覆盖密码保护文件场景."""
import os

from tests._runner import (assert_eq, assert_true, cleanup,
                            find_md_by_source, make_tmp_input, make_tmp_output,
                            parse_front_matter)
from tests import _samples

from doc2md import pipeline

CASES = []


def test_encrypted_pdf_defer():
    """加密 PDF → defer, 不崩溃, 原件保留."""
    import tempfile
    saved = (pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE)
    pipeline.MAX_WORKERS = 1
    pipeline.MAX_OCR_THREADS = 1
    pipeline.BATCH_MODE = None
    tmp = tempfile.mkdtemp()
    pdf_p = os.path.join(tmp, "secret.pdf")
    _samples.gen_pdf_encrypted(pdf_p)
    try:
        in_dir = make_tmp_input({"secret.pdf": open(pdf_p, "rb").read()})
        out = make_tmp_output()
        p, _, e = pipeline.run(in_dir, out)
        assert_eq(e, 0, "不应有错误 (加密=defer 非 error)")
        md = find_md_by_source(out, "original-doc/secret.pdf")
        fm = parse_front_matter(md)
        deferred = fm.get("deferred", [])
        types = [d["type"] for d in deferred]
        assert_true("encrypted" in types, f"deferred 应含 encrypted: {types}")
        assert_true("stage2:encrypted" in md, "md body 应有 stage2:encrypted 标记")
        orig = os.path.join(out, "original-doc", "secret.pdf")
        assert_true(os.path.exists(orig), "原件应保留")
    finally:
        (pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE) = saved
        cleanup(tmp, in_dir, out)


CASES.extend([
    ("pdf_encrypted_defer", test_encrypted_pdf_defer),
])
