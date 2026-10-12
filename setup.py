"""setup.py: 生成构建时间戳, 然后委托 pyproject.toml."""
from datetime import datetime, timezone
from pathlib import Path

_build = Path(__file__).parent / "doc2md" / "_build.py"
_build.write_text(
    f'"""自动生成, 勿手动编辑. pip install 时由 setup.py 写入."""\n'
    f'BUILD_TIME = "{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}"\n',
    encoding="utf-8",
)

from setuptools import setup  # noqa: E402
setup()
