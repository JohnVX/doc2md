"""运行全部测试的入口. 自动发现 tests/test_*.py.

用法:
    python tests/run_all.py          # 跑全部
    python tests/run_all.py -v       # 详细(含失败堆栈)
退出码: 全过 0, 有失败 1 (SKIP 不计为失败).
"""
import glob
import importlib
import os
import sys
import time
import traceback

# 工程根上 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows 控制台 utf-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from tests._runner import SkipTest  # noqa: E402


def _discover_modules():
    mods = []
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))):
        name = os.path.splitext(os.path.basename(path))[0]
        mods.append(f"tests.{name}")
    return mods


def main():
    verbose = "-v" in sys.argv[1:]
    results = []  # (name, status, detail)
    for mn in _discover_modules():
        try:
            mod = importlib.import_module(mn)
        except Exception as ex:
            results.append((mn, "FAIL", f"模块导入失败: {ex}"))
            continue
        short = mn.split(".")[-1]
        for name, fn in getattr(mod, "CASES", []):
            t0 = time.time()
            try:
                fn()
                results.append((f"{short}::{name}", "PASS",
                                f"{(time.time()-t0)*1000:.0f}ms"))
            except SkipTest as st:
                results.append((f"{short}::{name}", "SKIP", st.msg))
            except Exception as ex:
                tb = traceback.format_exc() if verbose else str(ex)
                results.append((f"{short}::{name}", "FAIL", tb))

    counts = {"PASS": 0, "SKIP": 0, "FAIL": 0}
    for _, st, _ in results:
        counts[st] = counts.get(st, 0) + 1
    width = max((len(n) for n, _, _ in results), default=10)
    print()
    for name, st, detail in results:
        mark = {"PASS": "PASS", "SKIP": "SKIP", "FAIL": "FAIL"}[st]
        extra = detail if st != "PASS" else detail
        print(f"  [{mark}] {name.ljust(width)}  {extra}")
    print()
    total = len(results)
    print(f"结果: {counts['PASS']}/{total} 通过, 跳过 {counts['SKIP']}, 失败 {counts['FAIL']}")
    sys.exit(0 if counts["FAIL"] == 0 else 1)


if __name__ == "__main__":
    main()
