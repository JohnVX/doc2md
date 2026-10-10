"""中断测试: batch 和 streaming 两种模式下, 中断都应优雅保存已处理部分."""
import os
import yaml

from tests._runner import assert_true, cleanup, make_tmp_input, make_tmp_output

from doc2md import parsers, pipeline

CASES = []


def test_interrupt_batch_parse():
    """batch 模式: 解析阶段中断, 已解析文件在 Phase 3 (pool shutdown 后) 写出."""
    in_dir = make_tmp_input({
        "a.txt": "文件A\n", "b.txt": "文件B\n", "c.txt": "文件C\n",
    })
    out = make_tmp_output()
    orig_w, orig_o, orig_b = pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE
    pipeline.MAX_WORKERS = 1
    pipeline.MAX_OCR_THREADS = 1
    pipeline.BATCH_MODE = True
    orig_get = parsers.get
    count = {"n": 0}

    def wrapped_get(h):
        fn = orig_get(h)
        if fn is None:
            return None

        def f(path, ctx=None):
            count["n"] += 1
            if count["n"] > 1:
                raise KeyboardInterrupt("mock 中断")
            return fn(path, ctx)
        return f

    parsers.get = wrapped_get
    try:
        pipeline.run(in_dir, out)
    except Exception as ex:
        raise AssertionError(f"中断应被捕获, 不应抛出: {ex!r}")
    finally:
        parsers.get = orig_get
        pipeline.MAX_WORKERS = orig_w
        pipeline.MAX_OCR_THREADS = orig_o
        pipeline.BATCH_MODE = orig_b

    man_path = os.path.join(out, "index", ".manifest.yaml")
    assert_true(os.path.exists(man_path), "manifest 应存在")
    man = yaml.safe_load(open(man_path, encoding="utf-8"))
    assert_true(len(man["entries"]) >= 1, "manifest 应有已处理条目")
    cleanup(in_dir, out)


def test_interrupt_streaming_parse():
    """streaming 模式: 解析阶段中断, 已解析文件在流式收尾时已写出."""
    in_dir = make_tmp_input({
        "a.txt": "文件A\n", "b.txt": "文件B\n", "c.txt": "文件C\n",
    })
    out = make_tmp_output()
    orig_w, orig_o, orig_b = pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE
    pipeline.MAX_WORKERS = 1
    pipeline.MAX_OCR_THREADS = 1
    pipeline.BATCH_MODE = False
    orig_get = parsers.get
    count = {"n": 0}

    def wrapped_get(h):
        fn = orig_get(h)
        if fn is None:
            return None

        def f(path, ctx=None):
            count["n"] += 1
            if count["n"] > 1:
                raise KeyboardInterrupt("mock 中断")
            return fn(path, ctx)
        return f

    parsers.get = wrapped_get
    try:
        pipeline.run(in_dir, out)
    except Exception as ex:
        raise AssertionError(f"中断应被捕获, 不应抛出: {ex!r}")
    finally:
        parsers.get = orig_get
        pipeline.MAX_WORKERS = orig_w
        pipeline.MAX_OCR_THREADS = orig_o
        pipeline.BATCH_MODE = orig_b

    man_path = os.path.join(out, "index", ".manifest.yaml")
    assert_true(os.path.exists(man_path), "manifest 应存在")
    man = yaml.safe_load(open(man_path, encoding="utf-8"))
    assert_true(len(man["entries"]) >= 1, "manifest 应有已处理条目")
    cleanup(in_dir, out)


def test_interrupt_batch_finalize():
    """batch 模式: Phase 3 (finalize) 阶段中断, 已写出文件保留."""
    in_dir = make_tmp_input({
        "a.txt": "文件A\n", "b.txt": "文件B\n", "c.txt": "文件C\n",
    })
    out = make_tmp_output()
    orig_w, orig_o, orig_b = pipeline.MAX_WORKERS, pipeline.MAX_OCR_THREADS, pipeline.BATCH_MODE
    orig_fin = pipeline._finalize
    pipeline.MAX_WORKERS = 1
    pipeline.MAX_OCR_THREADS = 1
    pipeline.BATCH_MODE = True
    count = {"n": 0}

    def wrapped_finalize(*args, **kwargs):
        count["n"] += 1
        if count["n"] > 1:
            raise KeyboardInterrupt("mock 中断")
        return orig_fin(*args, **kwargs)

    pipeline._finalize = wrapped_finalize
    try:
        pipeline.run(in_dir, out)
    except Exception as ex:
        raise AssertionError(f"中断应被捕获, 不应抛出: {ex!r}")
    finally:
        pipeline._finalize = orig_fin
        pipeline.MAX_WORKERS = orig_w
        pipeline.MAX_OCR_THREADS = orig_o
        pipeline.BATCH_MODE = orig_b

    man_path = os.path.join(out, "index", ".manifest.yaml")
    assert_true(os.path.exists(man_path), "manifest 应存在")
    man = yaml.safe_load(open(man_path, encoding="utf-8"))
    assert_true(len(man["entries"]) >= 1, "manifest 应有已处理条目")
    cleanup(in_dir, out)


CASES.append(("interrupt_batch_parse", test_interrupt_batch_parse))
CASES.append(("interrupt_streaming_parse", test_interrupt_streaming_parse))
CASES.append(("interrupt_batch_finalize", test_interrupt_batch_finalize))
