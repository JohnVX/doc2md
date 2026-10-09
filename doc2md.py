#!/usr/bin/env python3
"""资料转换处理器入口.

用法:
  doc2md                           # 零参数: 自动定位输入目录 + 输出到 ./knowledge/
  python doc2md.py                 # 同上
  python doc2md.py -i input -o knowledge -c config.yaml [--move] [-v]
"""
import sys

from doc2md.cli import main

if __name__ == "__main__":
    sys.exit(main())
