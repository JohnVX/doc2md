"""Batch 模式输出正确性 — 强制 batch, 验证产物与 streaming 一致."""
import os
import yaml

from tests._runner import (assert_eq, assert_true, cleanup, find_mds,
                            make_tmp_input, make_tmp_output)
from tests import _samples

from doc2md import pipeline

CASES = []

_SAVE = None


def _save_globals():
    return (pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE,
            pipeline.OCR_MODE, pipeline.OCR_PROCS, pipeline._ocr_pool)


def _restore_globals(saved):
    (pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE,
     pipeline.OCR_MODE, pipeline.OCR_PROCS, pipeline._ocr_pool) = saved


def _run_corpus(out, batch):
    import tempfile
    tmp = tempfile.mkdtemp()
    _samples.gen_docx(os.path.join(tmp, "doc.docx"))
    _samples.gen_xlsx(os.path.join(tmp, "tab.xlsx"))
    _samples.gen_pptx(os.path.join(tmp, "slides.pptx"))
    _samples.gen_pdf(os.path.join(tmp, "doc.pdf"))
    _samples.gen_md(os.path.join(tmp, "readme.md"))
    _samples.gen_txt(os.path.join(tmp, "note.txt"))
    in_dir = make_tmp_input({
        "doc.docx": open(os.path.join(tmp, "doc.docx"), "rb").read(),
        "tab.xlsx": open(os.path.join(tmp, "tab.xlsx"), "rb").read(),
        "slides.pptx": open(os.path.join(tmp, "slides.pptx"), "rb").read(),
        "doc.pdf": open(os.path.join(tmp, "doc.pdf"), "rb").read(),
        "readme.md": open(os.path.join(tmp, "readme.md"), "rb").read(),
        "note.txt": open(os.path.join(tmp, "note.txt"), "rb").read(),
    })
    cleanup(tmp)
    pipeline.MAX_WORKERS = 2
    pipeline.MAX_OCR_THREADS = 1
    pipeline.BATCH_MODE = batch
    pipeline.OCR_MODE = None
    pipeline.OCR_PROCS = None
    pipeline._ocr_pool = None
    p, s, e = pipeline.run(in_dir, out)
    return in_dir, p, s, e


def test_batch_produces_correct_output():
    """Batch 模式: 6 格式全部处理, catalog 完整, 0 错误."""
    saved = _save_globals()
    out = make_tmp_output()
    try:
        in_dir, p, s, e = _run_corpus(out, True)
        assert_eq(e, 0, "batch 不应有错误")
        assert_eq(p, 6, "6 文件都应处理")
        mds = find_mds(out)
        assert_eq(len(mds), 6, "6 个 md 产物")
        cat = yaml.safe_load(open(os.path.join(out, "index", "catalog.yaml")))
        assert_eq(len(cat["documents"]), 6, "catalog 6 文档")
        types = sorted(d["source_type"] for d in cat["documents"])
        assert_eq(types, ["docx", "md", "pdf", "pptx", "txt", "xlsx"])
    finally:
        _restore_globals(saved)
        cleanup(out)


def test_batch_and_streaming_same_doc_count():
    """同一输入, batch vs streaming, 产物数和 doc-id 一致."""
    saved = _save_globals()
    out_b = make_tmp_output()
    out_s = make_tmp_output()
    try:
        _, p_b, _, e_b = _run_corpus(out_b, True)
        assert_eq(e_b, 0, "batch 无错误")
        _, p_s, _, e_s = _run_corpus(out_s, False)
        assert_eq(e_s, 0, "streaming 无错误")
        assert_eq(p_b, p_s, "处理数应一致")
        mds_b = sorted(os.path.basename(m) for m in find_mds(out_b))
        mds_s = sorted(os.path.basename(m) for m in find_mds(out_s))
        assert_eq(mds_b, mds_s, "doc-id (文件名) 应一致")
    finally:
        _restore_globals(saved)
        cleanup(out_b, out_s)


CASES.extend([
    ("batch_correct_output", test_batch_produces_correct_output),
    ("batch_streaming_same_count", test_batch_and_streaming_same_doc_count),
])
