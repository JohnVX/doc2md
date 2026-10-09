"""确定性格式识别器.

只依赖文件内容的二进制签名 / zip 内部结构, 不靠扩展名.
返回 (format, handler, meta):
  format  - 具体格式名 (png/jpeg/docx/pptx/xlsx/pdf/md/txt/code/empty/binary/ole2/...)
  handler - 路由到哪个解析器 (image/text/docx/pptx/xlsx/pdf/md/None)
  meta    - 附加信息 (encoding 等)

编码嗅探用增量解码器(final=False), 避免 >64KB 文件因截断多字节字符而误判.
含 null 字节的文件判为 binary; 空文件判为 empty.
"""
import codecs
import os
import re
import zipfile

# 文本类: 代码扩展名 -> 语言标注
_CODE_EXTS = {
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".py": "python", ".sh": "bash", ".bash": "bash",
    ".zsh": "bash", ".cmake": "cmake", ".java": "java", ".go": "go",
    ".rs": "rust", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".json": "json",
    ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".ini": "ini",
    ".cfg": "ini", ".conf": "ini", ".toml": "toml", ".sql": "sql",
    ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
    ".scala": "scala", ".clj": "clojure", ".lua": "lua", ".pl": "perl",
    ".r": "r", ".m": "objc", ".mm": "objc", ".cs": "csharp", ".vb": "vb",
}
# 按文件名主干识别为代码 (不看扩展名, 故 cmake/make/docker 都算)
_CODE_STEMS = {
    "cmakelists": "cmake", "makefile": "make", "gnumakefile": "make",
    "dockerfile": "docker", "rakefile": "ruby", "gemfile": "ruby",
    "brewfile": "ruby", "podfile": "ruby",
}

_MD_EXTS = {".md", ".markdown", ".mdown", ".markdn"}


def _looks_like_markdown(sample: str) -> bool:
    """样本里命中 >=2 种 markdown 特征才算 md."""
    signals = [
        r"^\s{0,3}#{1,6}\s+\S",
        r"^\s{0,3}[-*+]\s+\S",
        r"^\s{0,3}\d+\.\s+\S",
        r"```",
        r"!\[[^\]]*\]\(",
        r"\[[^\]]+\]\([^)]+\)",
        r"^\|.*\|\s*$",
        r"^\s{0,3}>\s+\S",
    ]
    hits = 0
    for line in sample.splitlines()[:200]:
        for pat in signals:
            if re.search(pat, line):
                hits += 1
                break
        if hits >= 2:
            return True
    return hits >= 2


def _looks_like_code(sample: str) -> bool:
    """样本里有 >=3 行代码特征且无明显 md 结构, 判为代码."""
    pats = [
        r"^\s*(if|for|while|return|else|elif|endif|endforeach|endforeach|"
        r"function|endfunction|def|class|func|fn|import|from|package|"
        r"include|using|namespace|public|private|var|let|const|val|struct|enum)\b",
        r"^\s*#include\b",
        r"^\s*cmake_minimum_required\b",
        r"^\s*[\w.]+\s*\(.*\)\s*$",   # 形如 func(...) 的调用/定义行
        r";\s*$",                      # 行尾分号
        r"^\s*[{}]\s*$",               # 单独花括号行
    ]
    hits = 0
    for line in sample.splitlines()[:200]:
        for pat in pats:
            if re.search(pat, line):
                hits += 1
                break
    return hits >= 3 and not _looks_like_markdown(sample)


def _try_decode(sample: bytes, cand: str) -> bool:
    """用增量解码器试探 sample 是否符合 cand 编码.

    关键: final=False 不要求末尾完整, 截断在多字节字符中间不会报错
    (只缓冲尾部不完整字节), 避免 >64KB 文件因 64KB 截断而误降到 latin-1.
    """
    try:
        dec = codecs.getincrementaldecoder(cand)(errors="strict")
        dec.decode(sample, final=False)
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def _sniff_text(path: str, ext: str, name: str, stem: str):
    """文本类细分: md / code / txt. 返回 (format, handler, meta)."""
    # 读前 64KB 做编码嗅探与内容特征判定
    with open(path, "rb") as f:
        raw = f.read(65536)
    # 二进制兜底: 含 null 字节几乎必是二进制(文本不会含 \x00), 避免当 txt 解出乱码
    if b"\x00" in raw[:8192]:
        return ("binary", None, {"reason": "疑似二进制文件(含 null 字节)"})
    enc = None
    for cand in ("utf-8", "gbk", "gb18030", "latin-1"):
        if _try_decode(raw, cand):
            enc = cand
            break
    if enc is None:
        enc = "latin-1"
    try:
        s = raw.decode(enc, errors="ignore")
    except Exception:
        s = ""
    # 1) 扩展名明确为代码
    if ext in _CODE_EXTS:
        return ("code", "text", {"lang": _CODE_EXTS[ext], "encoding": enc})
    # 2) 文件名主干明确为代码 (cmakelists/makefile/dockerfile..., 不看扩展名)
    if stem in _CODE_STEMS:
        return ("code", "text", {"lang": _CODE_STEMS[stem], "encoding": enc})
    # 3) 内容优先: 像代码 -> code; 像 md -> md
    if _looks_like_code(s):
        return ("code", "text", {"lang": _guess_code_lang(s, ext), "encoding": enc})
    if _looks_like_markdown(s):
        return ("md", "md", {"encoding": enc})
    # 4) 扩展名为 md 但内容无特征 -> 仍按 md (尊重扩展名)
    if ext in _MD_EXTS:
        return ("md", "md", {"encoding": enc})
    # 5) 兜底纯文本
    return ("txt", "text", {"encoding": enc})


def _guess_code_lang(sample: str, ext: str):
    """无明确扩展名时, 按内容特征粗猜语言标注 (仅影响围栏高亮, 不影响正确性)."""
    if "cmake_minimum_required" in sample or re.search(r"\badd_executable\b", sample):
        return "cmake"
    if re.search(r"^\s*def\s+\w+", sample) or re.search(r"\bimport\s+\w+", sample):
        return "python"
    if re.search(r"^\s*#include\b", sample):
        return "c"
    if re.search(r"\bfn\s+\w+", sample) or re.search(r"\blet\s+mut\b", sample):
        return "rust"
    if re.search(r"\bfunc\s+\w+", sample):
        return "go"
    return "text"


def detect(path: str):
    """识别文件格式. 返回 dict: {format, handler, ext, meta}."""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    stem = os.path.splitext(name)[0]
    # 空文件
    try:
        if os.path.getsize(path) == 0:
            return {"format": "empty", "handler": None, "ext": ext,
                    "meta": {"reason": "空文件"}}
    except OSError:
        pass
    with open(path, "rb") as f:
        head = f.read(8)
    # 二进制: OOXML (zip)
    if head[:2] == b"PK" and head[2:4] in (b"\x03\x04", b"\x05\x06", b"\x07\x08"):
        try:
            z = zipfile.ZipFile(path)
            names = set(z.namelist())
            if "word/document.xml" in names:
                return {"format": "docx", "handler": "docx", "ext": ext, "meta": {}}
            if any(n.startswith("xl/") and n.endswith("workbook.xml") for n in names) or "xl/workbook.xml" in names:
                return {"format": "xlsx", "handler": "xlsx", "ext": ext, "meta": {}}
            if "ppt/presentation.xml" in names:
                return {"format": "pptx", "handler": "pptx", "ext": ext, "meta": {}}
            return {"format": "zip", "handler": None, "ext": ext,
                    "meta": {"reason": "zip but not office OOXML"}}
        except zipfile.BadZipFile:
            return {"format": "zip-corrupt", "handler": None, "ext": ext, "meta": {}}
    # 二进制: 旧版 OLE2 (doc/xls/ppt)
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return {"format": "ole2", "handler": None, "ext": ext,
                "meta": {"reason": "old binary office (doc/xls/ppt), stage1 不支持"}}
    # 二进制: PDF
    if head[:4] == b"%PDF":
        return {"format": "pdf", "handler": "pdf", "ext": ext, "meta": {}}
    # 二进制: 图片
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return {"format": "png", "handler": "image", "ext": ext, "meta": {}}
    if head[:3] == b"\xff\xd8\xff":
        return {"format": "jpeg", "handler": "image", "ext": ext, "meta": {}}
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return {"format": "gif", "handler": "image", "ext": ext, "meta": {}}
    if head[:4] == b"RIFF" and open(path, "rb").read(12)[8:12] == b"WEBP":
        return {"format": "webp", "handler": "image", "ext": ext, "meta": {}}
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return {"format": "tiff", "handler": "image", "ext": ext, "meta": {}}
    if head[:2] == b"BM":
        return {"format": "bmp", "handler": "image", "ext": ext, "meta": {}}
    # 其余: 文本类
    fmt, handler, meta = _sniff_text(path, ext, name, stem)
    return {"format": fmt, "handler": handler, "ext": ext, "meta": meta}


def supported_handlers():
    """本环境已具备能力的 handler 集合."""
    return {"md", "text", "docx", "pptx", "xlsx", "pdf", "image"}
