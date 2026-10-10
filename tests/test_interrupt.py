"""中断测试: mock 第2个文件抛 KeyboardInterrupt, 应捕获并保存已处理部分."""
import os
import yaml

from tests._runner import assert_eq, assert_true, cleanup, make_tmp_input, make_tmp_output

from doc2md import parsers, pipeline

CASES = []


def test_interrupt_saves_partial():
    # 造几个 txt (无需库, 便宜), 让循环有多个文件
    in_dir = make_tmp_input({
        "a.txt": "文件A\n", "b.txt": "文件B\n", "c.txt": "文件C\n",
    })
    out = make_tmp_output()
    # ProcessPool 模式下每个子进程有独立的 count 副本;
    # 设 MAX_WORKERS=1 确保单进程顺序处理, mock 的 count 累积才生效
    orig_max = pipeline.MAX_WORKERS
    pipeline.MAX_WORKERS = 1
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
        # 不应抛出未捕获异常
        pipeline.run(in_dir, out)
    except Exception as ex:
        raise AssertionError(f"中断应被捕获, 不应抛出: {ex!r}")
    finally:
        parsers.get = orig_get
        pipeline.MAX_WORKERS = orig_max

    # manifest/catalog 应已保存, 含 >=1 条目
    man_path = os.path.join(out, "index", ".manifest.yaml")
    cat_path = os.path.join(out, "index", "catalog.yaml")
    assert_true(os.path.exists(man_path), "manifest 应存在")
    assert_true(os.path.exists(cat_path), "catalog 应存在")
    man = yaml.safe_load(open(man_path, encoding="utf-8"))
    assert_true(len(man["entries"]) >= 1, "manifest 应有已处理条目")
    cleanup(in_dir, out)


CASES.append(("interrupt_saves_partial", test_interrupt_saves_partial))
