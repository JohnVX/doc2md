"""零参数模式额外场景: 增量重跑, config在输入目录, 嵌套子目录."""
import os
import tempfile

from tests._runner import assert_eq, assert_true, cleanup, find_mds

from doc2md import cli

CASES = []


def _chdir(d):
    class _Ctx:
        def __enter__(self):
            self._old = os.getcwd()
            os.chdir(d)
            return self
        def __exit__(self, *a):
            os.chdir(self._old)
    return _Ctx()


def test_zero_arg_incremental():
    """零参数跑 -> 加文件 -> 零参数再跑 -> 只处理新增."""
    root = tempfile.mkdtemp(prefix="d2m_za_")
    in_dir = os.path.join(root, "src")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "a.txt"), "w") as f:
        f.write("a\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            cli.main([])
        assert_eq(len(find_mds(out_dir)), 1)
        # 加新文件
        with open(os.path.join(in_dir, "b.txt"), "w") as f:
            f.write("b\n")
        with _chdir(root):
            cli.main([])
        assert_eq(len(find_mds(out_dir)), 2, "应处理新增文件")
        cleanup(root)
    except Exception:
        cleanup(root)
        raise


CASES.append(("zero_arg_incremental", test_zero_arg_incremental))


def test_config_in_input_dir():
    """config.yaml 在输入目录而非 CWD -> 也能自动找到."""
    root = tempfile.mkdtemp(prefix="d2m_za_")
    in_dir = os.path.join(root, "data")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "x.txt"), "w") as f:
        f.write("x\n")
    # config 在输入目录
    with open(os.path.join(in_dir, "config.yaml"), "w") as f:
        f.write("taxonomy: [custom]\ndefault_category: custom\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 0)
        import yaml
        cat = yaml.safe_load(open(os.path.join(out_dir, "index", "catalog.yaml")))
        assert_true("custom" in cat["categories"], "应使用输入目录的 config")
        cleanup(root)
    except Exception:
        cleanup(root)
        raise


CASES.append(("config_in_input_dir", test_config_in_input_dir))


def test_nested_subdirs():
    """文档在子目录的子目录 -> 零参数仍能定位顶层目录, 递归处理."""
    root = tempfile.mkdtemp(prefix="d2m_za_")
    in_dir = os.path.join(root, "materials")
    sub = os.path.join(in_dir, "sub1", "sub2")
    os.makedirs(sub)
    with open(os.path.join(sub, "deep.txt"), "w") as f:
        f.write("deep content\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 0)
        assert_eq(len(find_mds(out_dir)), 1, "应递归找到嵌套文件")
        cleanup(root)
    except Exception:
        cleanup(root)
        raise


CASES.append(("nested_subdirs", test_nested_subdirs))


def test_zero_arg_then_move_then_empty():
    """零参数 --move 后输入清空, 再跑应报无文档."""
    root = tempfile.mkdtemp(prefix="d2m_za_")
    in_dir = os.path.join(root, "src")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "a.txt"), "w") as f:
        f.write("a\n")
    out_dir = os.path.join(root, "knowledge")
    try:
        with _chdir(root):
            cli.main(["--move"])   # 移动模式, 输入清空
        assert_eq(len(find_mds(out_dir)), 1)
        assert_eq(len(os.listdir(in_dir)), 0, "move 后输入应空")
        # 再跑: 无文档
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 1, "空输入应返回 1")
        cleanup(root)
    except Exception:
        cleanup(root)
        raise


CASES.append(("zero_arg_then_move_then_empty", test_zero_arg_then_move_then_empty))


def test_output_dir_auto_created():
    """输出目录不存在 -> 零参数自动创建."""
    root = tempfile.mkdtemp(prefix="d2m_za_")
    in_dir = os.path.join(root, "docs")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "a.txt"), "w") as f:
        f.write("a\n")
    out_dir = os.path.join(root, "knowledge")
    assert_true(not os.path.exists(out_dir), "输出目录初始不存在")
    try:
        with _chdir(root):
            rc = cli.main([])
        assert_eq(rc, 0)
        assert_true(os.path.isdir(out_dir), "应自动创建输出目录")
        cleanup(root)
    except Exception:
        cleanup(root)
        raise


CASES.append(("output_dir_auto_created", test_output_dir_auto_created))
