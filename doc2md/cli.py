"""CLI 入口.

零参数模式: 敲 doc2md 即在 CWD 下启发式定位输入目录 + 默认输出 knowledge/ + 自动找 config.yaml.
显式参数: -i/-o/-c 覆盖自动检测, 可任意混用 (只指定需要的, 其余自动).
"""
import argparse
import logging
import sys
from pathlib import Path

from . import pipeline

log = logging.getLogger("cli")

_DOC_EXTS = {".pptx", ".docx", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg",
             ".md", ".txt", ".ppt", ".doc", ".xls",
             ".c", ".h", ".py", ".sh", ".cmake", ".yaml", ".yml"}
_EXCLUDE_NAMES = {"knowledge", "output", "out", "build", "dist",
                  "__pycache__", ".git", "node_modules", ".venv", "venv"}
_OUTPUT_MARKER = Path("index") / "catalog.yaml"


def _utf8_stdio():
    """Windows 控制台默认编码可能非 utf-8, 重配以正确显示中文日志."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _count_docs(d, max_files=5000, max_docs=200):
    """递归统计目录下文档文件数 (有上限防超慢扫描)."""
    n = 0
    total = 0
    try:
        for p in d.rglob("*"):
            total += 1
            if total > max_files or n > max_docs:
                break
            if p.is_file() and p.suffix.lower() in _DOC_EXTS:
                n += 1
    except Exception:
        pass
    return n


def _find_input_dir(cwd):
    """在 cwd 的直接子目录中启发式查找含文档最多的目录.

    排除: 输出目录(含 index/catalog.yaml) / 构建/缓存目录 / 隐藏目录 / 符号链接.
    返回路径字符串或 None.
    """
    candidates = []
    try:
        entries = list(Path(cwd).iterdir())
    except Exception as e:
        raise RuntimeError(f"扫描当前目录失败: {e}")
    for d in entries:
        if not d.is_dir() or d.is_symlink():
            continue
        if d.name.startswith(".") or d.name.lower() in _EXCLUDE_NAMES:
            continue
        if (d / _OUTPUT_MARKER).exists():
            continue
        n = _count_docs(d)
        if n > 0:
            candidates.append((n, str(d)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1]


def _find_config(cwd, input_dir):
    """CWD 优先, 其次输入目录, 找 config.yaml."""
    for d in (Path(cwd), Path(input_dir)):
        p = d / "config.yaml"
        if p.is_file():
            return str(p)
    return None


def _print_auto(items):
    """统一打印 [auto] 行."""
    for line in items:
        print(f"[auto] {line}")


def main(argv=None):
    """CLI 入口: 解析参数、自动检测、调用 pipeline."""
    _utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="doc2md",
        description="资料转换处理器: 原始资料 -> knowledge/ (yaml 索引 + md 文本库 + stage2 交接)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "零参数模式 (推荐):\n"
            "  doc2md                    # 自动定位含文档的子目录为输入\n"
            "                           # 输出到 ./knowledge/, 自动查找 config.yaml\n\n"
            "显式参数:\n"
            "  doc2md -i input -o knowledge -c config.yaml\n"
            "  doc2md -i input --move -v\n"
            "  doc2md                    # 混用: 只指定需要的, 其余自动"
        ))
    ap.add_argument("-i", "--input", default=None, help="输入目录 (不指定则自动检测)")
    ap.add_argument("-o", "--output", default=None, help="输出目录 (不指定则 ./knowledge/)")
    ap.add_argument("-c", "--config", default=None, help="配置文件 yaml (不指定则自动查找 config.yaml)")
    ap.add_argument("--move", action="store_true", help="移动原文件而非复制 (一次性摄取, 清空输入)")
    ap.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = ap.parse_args(argv)

    cwd = Path.cwd()
    input_dir = args.input
    output_dir = args.output
    config_path = args.config
    auto = []

    # --- 输入目录 ---
    if input_dir is None:
        try:
            input_dir = _find_input_dir(cwd)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            print("请用 -i <目录> 显式指定输入目录", file=sys.stderr)
            return 1
        if input_dir is None:
            print("error: 在当前目录下未找到含文档的子目录", file=sys.stderr)
            print("请将文档放入子目录, 或用 -i <目录> 指定输入", file=sys.stderr)
            print("提示: 如需处理当前目录下的文档, 用 -i . 指定", file=sys.stderr)
            ap.print_help(file=sys.stderr)
            return 1
        auto.append(f"输入目录: {input_dir}")
    elif not Path(input_dir).is_dir():
        print(f"error: 输入目录不存在: {input_dir}", file=sys.stderr)
        return 1

    # --- 输出目录 ---
    if output_dir is None:
        output_dir = str(cwd / "knowledge")
        auto.append(f"输出目录: {output_dir}")

    # 输入与输出不能同一目录
    if Path(input_dir).resolve() == Path(output_dir).resolve():
        print(f"error: 输入与输出不能是同一目录: {input_dir}", file=sys.stderr)
        return 1

    # 尝试创建输出目录
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"error: 无权限创建输出目录: {output_dir}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: 创建输出目录失败: {output_dir}: {e}", file=sys.stderr)
        return 1

    # --- 配置 ---
    if config_path is None:
        config_path = _find_config(cwd, input_dir)
        if config_path:
            auto.append(f"配置: {config_path}")
        else:
            auto.append("配置: 无 (全部落 unclassified)")
    elif not Path(config_path).is_file():
        print(f"error: 配置文件不存在: {config_path}", file=sys.stderr)
        return 1

    _print_auto(auto)

    # --- 执行 ---
    _, _, errors = pipeline.run(input_dir, output_dir, config_path,
                                move=args.move, verbose=args.verbose)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
