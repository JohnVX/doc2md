"""_finalize 异常不中断整轮: mock _finalize 抛 RuntimeError, 验证 pipeline 继续处理剩余文件."""
import os

from tests._runner import assert_eq, assert_true, cleanup, find_mds, make_tmp_input, make_tmp_output

from doc2md import pipeline

CASES = []


def test_finalize_exception_does_not_crash_pipeline():
    """_finalize 抛非 KeyboardInterrupt 异常 -> 计 errors, 继续处理剩余文件."""
    in_dir = make_tmp_input({
        "a.txt": "文件A\n",
        "b.txt": "文件B\n",
        "c.txt": "文件C\n",
    })
    out = make_tmp_output()
    saved = (pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE)
    pipeline.MAX_WORKERS = 1
    pipeline.MAX_OCR_THREADS = 1
    pipeline.BATCH_MODE = False
    orig_finalize = pipeline._finalize
    count = {"n": 0}

    def wrapped_finalize(*args, **kwargs):
        count["n"] += 1
        if count["n"] == 1:
            raise RuntimeError("mock 收尾崩溃")
        return orig_finalize(*args, **kwargs)

    pipeline._finalize = wrapped_finalize
    try:
        p, s, e = pipeline.run(in_dir, out)
        assert_eq(e, 1, "第一个文件 finalize 崩溃应计 1 error")
        assert_eq(p, 2, "剩余 2 个文件应正常处理")
        mds = find_mds(out)
        assert_eq(len(mds), 2, "应有 2 个 md 产物 (崩溃的不产生)")
    finally:
        pipeline._finalize = orig_finalize
        (pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE) = saved
        cleanup(in_dir, out)


CASES.append(("finalize_exception_continues", test_finalize_exception_does_not_crash_pipeline))
