"""零参数模式测试: doc2md 无参数时启发式定位输入/输出/config."""
import os
import shutil
import tempfile

from tests._runner import assert_eq, assert_true, cleanup, find_mds, make_tmp_output

from doc2md import cli

CASES = []


def _chdir(d):
    """返回一个可用的 cwd context."""
    class _Ctx:
        def __enter__(self):
            self._old = os.getcwd()
            os.chdir(d)
            return self
        def __exit__(self, *a):
            os.chdir(self._old)
    return _Ctx()


def test_zero_arg_finds_input_and_output():
    """CWD 下有含文档子目录 -> 自动定位为输入, 创建 ./knowledge/ 为输出."""
    root = tempfile.mkdtemp(prefix="d2m_zero_")
    in_dir = os.path.join(root, "materials")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "note.txt"), "w") as f:
        f.write("hello\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 0, "零参数模式应成功")
        assert_true(os.path.isdir(out_dir), "应创建 knowledge/ 输出目录")
        assert_true(len(find_mds(out_dir)) >= 1, "应有 md 产物")
    finally:
        cleanup(root)


CASES.append(("zero_arg_finds_input_and_output", test_zero_arg_finds_input_and_output))


def test_zero_arg_finds_config():
    """CWD 有 config.yaml -> 自动使用."""
    root = tempfile.mkdtemp(prefix="d2m_zero_")
    in_dir = os.path.join(root, "data")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "a.txt"), "w") as f:
        f.write("text\n")
    with open(os.path.join(root, "config.yaml"), "w") as f:
        f.write("taxonomy: [cat1]\ndefault_category: cat1\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 0)
        cat = yaml.safe_load(open(os.path.join(out_dir, "index", "catalog.yaml")))
        assert_true("cat1" in cat["categories"], "应使用 config.yaml 分类")
    finally:
        cleanup(root)


CASES.append(("zero_arg_finds_config", test_zero_arg_finds_config))


def test_zero_arg_no_docs_error():
    """CWD 下无含文档子目录 -> 报错退出码 1."""
    root = tempfile.mkdtemp(prefix="d2m_zero_")
    os.makedirs(os.path.join(root, "empty"))
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 1, "无文档应返回 1")
    finally:
        cleanup(root)


CASES.append(("zero_arg_no_docs_error", test_zero_arg_no_docs_error))


def test_zero_arg_excludes_output_dir():
    """已有 knowledge/ 输出目录不被误选为输入."""
    root = tempfile.mkdtemp(prefix="d2m_zero_")
    in_dir = os.path.join(root, "src")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "x.txt"), "w") as f:
        f.write("x\n")
    # 先跑一次产生 knowledge/
    out_dir = os.path.join(root, "knowledge")
    with _chdir(root):
        cli.main([])
    # 再跑: 应选 src/ 不选 knowledge/
    mds_before = find_mds(out_dir)
    with _chdir(root):
        rc = cli.main([])
    assert_eq(rc, 0)
    assert_eq(len(find_mds(out_dir)), len(mds_before), "不应重复处理 knowledge/")
    cleanup(root)


CASES.append(("zero_arg_excludes_output_dir", test_zero_arg_excludes_output_dir))


def test_zero_arg_picks_most_docs():
    """多个含文档子目录 -> 选文档最多的."""
    root = tempfile.mkdtemp(prefix="d2m_zero_")
    d1 = os.path.join(root, "aaa")
    d2 = os.path.join(root, "bbb")
    os.makedirs(d1)
    os.makedirs(d2)
    for i in range(3):
        with open(os.path.join(d1, f"f{i}.txt"), "w") as f:
            f.write(f"text{i}\n")
    with open(os.path.join(d2, "single.txt"), "w") as f:
        f.write("one\n")
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 0)
        # aaa 有 3 个文档, bbb 有 1 个 -> 选 aaa
        src_cat = os.path.join(root, "knowledge", "index", "catalog.yaml")
        import yaml as _y
        cat = _y.safe_load(open(src_cat))
        assert_eq(len(cat["documents"]), 3, "应处理 aaa 的 3 个文件")
    finally:
        cleanup(root)


CASES.append(("zero_arg_picks_most_docs", test_zero_arg_picks_most_docs))


def test_mixed_args_auto_fill():
    """只指定 -i, 输出和 config 自动."""
    root = tempfile.mkdtemp(prefix="d2m_zero_")
    in_dir = os.path.join(root, "raw")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "a.txt"), "w") as f:
        f.write("a\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            rc = cli.main(["-i", in_dir])
        assert_eq(rc, 0)
        assert_true(os.path.isdir(out_dir), "应自动创建 knowledge/")
    finally:
        cleanup(root)


CASES.append(("mixed_args_auto_fill", test_mixed_args_auto_fill))


import yaml  # noqa: E402
