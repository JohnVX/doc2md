#!/bin/bash
# 对比两份 doc2md 产物目录, 判断是否"内容一致"(排除天然不同的时间/构建字段).
#
# 用法:
#   bash tests/compare_outputs.sh <目录A> <目录B>
#
# 典型场景: 同一批输入在 Linux(WSL) 与 Windows 上各跑一次, 判断产物是否一致。
# WSL 下可同时访问两侧, 如:
#   bash tests/compare_outputs.sh \
#       /home/shuwei/test/knowledge \
#       /mnt/c/Users/w00670216/Downloads/knowledge
#
# 判定口径:
#   应一致  — 正文/表格/分类/摘要/doc-id/OCR 文本/图片资产 (纯转换结果)
#   不必一致 — generated_at (运行时刻); 两侧文件权限/大小写敏感性
#
# 退出码: 0 = 无明显差异; 1 = 存在差异 (便于 CI/脚本判断)

set -u
A="${1:-}"; B="${2:-}"
if [ -z "$A" ] || [ -z "$B" ]; then
    echo "用法: $0 <目录A> <目录B>" >&2
    exit 2
fi
if [ ! -d "$A" ] || [ ! -d "$B" ]; then
    echo "错误: 目录不存在 ($A / $B)" >&2
    exit 2
fi

# 归一化单文件: 删运行时刻字段, 统一行尾 (CRLF->LF), 去尾随空白差异
norm() {
    sed -e '/^generated_at:/d' -e 's/[[:space:]]*$//' -e 's/\r$//' "$1"
}

rc=0

echo "== 1. 文件清单 =="
if diff <(cd "$A" && find . -type f | sort) \
        <(cd "$B" && find . -type f | sort); then
    echo "   (文件清单一致)"
else
    echo "   ^ 仅在一侧存在的文件 (见上)"
    rc=1
fi

echo
echo "== 2. 文本类产物逐文件对比 (排除 generated_at/行尾) =="
same=0; diffn=0
while IFS= read -r f; do
    fa="$A/$f"; fb="$B/$f"
    case "$f" in
        *.md|*.yaml|*.yml|*.txt|*.json)
            if [ ! -f "$fb" ]; then
                echo "   [缺] $f"; diffn=$((diffn+1)); rc=1; continue
            fi
            if diff <(norm "$fa") <(norm "$fb") >/dev/null 2>&1; then
                same=$((same+1))
            else
                echo "   [异] $f"
                diffn=$((diffn+1)); rc=1
            fi
            ;;
    esac
done < <(cd "$A" && find . -type f | sort)
echo "   相同 $same 个, 不同 $diffn 个"

echo
echo "== 3. 二进制资产 (图片) 内容哈希 =="
# 用 sha256; 只比资产 (排除文本类)
hashset() {
    (cd "$1" && find . -type f \
        \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \
           -o -name '*.gif' -o -name '*.bmp' -o -name '*.tif*' -o -name '*.webp' \
           -o -name '*.wmf' -o -name '*.emf' \) \
        -exec sha256sum {} \; 2>/dev/null | sort)
}
if diff <(hashset "$A") <(hashset "$B") >/dev/null; then
    echo "   (资产哈希一致)"
else
    echo "   ^ 资产差异:"; diff <(hashset "$A") <(hashset "$B") | head -30
    rc=1
fi

echo
if [ "$rc" -eq 0 ]; then
    echo "结论: 两产物在排除时间戳后内容一致。"
else
    echo "结论: 存在差异 (上面标记 [异]/[缺] 或资产差异)。"
    echo "提示: 若差异只在 OCR 文本, 多为浮点/线程调度噪声 (进程 vs 线程模式);"
    echo "      若结构/分类不同, 需进一步排查平台相关逻辑。"
fi
exit "$rc"
