"""测试工具 (仅 stdlib). 提供临时目录/断言/日志捕获/跳过/路径修正 + md 检索."""
import contextlib
import glob
import logging
import os
import shutil
import sys
import tempfile

# 把工程根加入 sys.path, 让每个测试模块可直接 import doc2md / tests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---- OCR 可用性探测 (共享, 避免各 test 重复 try-import) ----
try:
    import rapidocr_onnxruntime as _rapid  # noqa: F401
    HAS_RAPIDOCR = True
except Exception:
    HAS_RAPIDOCR = False


class SkipTest(Exception):
    """测试跳过信号. run_all 捕获后记 SKIP 而非 FAIL."""
    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg


def skip(msg=""):
    raise SkipTest(msg or "跳过")


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "断言失败")


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


def make_tmp_input(files=None):
    """创建临时输入目录. files: {name: bytes|str} (str 按 utf-8 写). 返回目录路径."""
    d = tempfile.mkdtemp(prefix="d2m_in_")
    if files:
        for name, content in files.items():
            p = os.path.join(d, name)
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if isinstance(content, bytes):
                with open(p, "wb") as f:
                    f.write(content)
            else:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)
    return d


def make_tmp_output():
    return tempfile.mkdtemp(prefix="d2m_out_")


def cleanup(*paths):
    for p in paths:
        shutil.rmtree(p, ignore_errors=True)


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.msgs = []

    def emit(self, record):
        self.msgs.append(record.getMessage())

    def has(self, kw):
        return any(kw in m for m in self.msgs)


@contextlib.contextmanager
def capture_logs(*logger_names):
    """捕获指定 logger 的全部日志消息 (DEBUG+), 退出时还原 level."""
    cap = _Cap()
    loggers = [logging.getLogger(n) for n in logger_names]
    saved = {n: logging.getLogger(n).level for n in logger_names}
    for lg in loggers:
        lg.addHandler(cap)
        lg.setLevel(logging.DEBUG)
    try:
        yield cap
    finally:
        for lg in loggers:
            lg.removeHandler(cap)
        for n, lv in saved.items():
            logging.getLogger(n).setLevel(lv)


def input_examples_dir():
    return os.path.join(ROOT, "input-examples")


# ---- md 产物检索与断言 ----
def require_ocr():
    """rapidocr 未安装时跳过测试(OCR 为可选增强, 不应让测试失败)."""
    if not HAS_RAPIDOCR:
        skip("rapidocr 未安装, OCR 测试跳过(OCR 为可选增强)")


def find_mds(out):
    """输出目录下所有 md 文件路径(按路径排序)."""
    return sorted(glob.glob(os.path.join(out, "docs", "*", "*.md")))


def read_md(out, n=0):
    """读第 n 个(默认首个) md 全文."""
    mds = find_mds(out)
    if not mds:
        raise AssertionError(f"无 md 产物于 {out}")
    with open(mds[n], encoding="utf-8") as f:
        return f.read()


def parse_front_matter(md_text):
    """解析 md front-matter 返回 dict; 无则 raise AssertionError."""
    import re
    import yaml
    m = re.match(r"^---\n(.*?)\n---\n", md_text, re.S)
    if not m:
        raise AssertionError("md 缺 front-matter")
    return yaml.safe_load(m.group(1))


def find_md_by_source(out, source):
    """按 front-matter 的 source 字段定位 md, 返回全文."""
    for p in find_mds(out):
        with open(p, encoding="utf-8") as f:
            txt = f.read()
        try:
            if parse_front_matter(txt).get("source") == source:
                return txt
        except Exception:
            continue
    raise AssertionError(f"未找到 source={source} 的 md")


def assert_contains_any(text, keywords, msg=""):
    low = text.lower()
    if not any(kw.lower() in low for kw in keywords):
        raise AssertionError(f"{msg}: 无任一关键词 {list(keywords)} 出现, 抽样: {text[:120]!r}")
