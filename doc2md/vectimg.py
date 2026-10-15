"""矢量图 → 位图渲染: 把 EMF/WMF 等矢量图转为 PNG, 供 OCR 与 stage2 使用.

为什么需要:
  EMF/WMF 是 Office 里常见的矢量图 (Visio/SmartArt/剪贴画), 原始格式多模态
  大模型和 OCR 引擎都读不了. stage1 做格式归一化 (不涉及"理解"), 把它转成
  PNG 位图, 下游即可直接使用 —— 符合"stage1 是忠实结构转换器"的定位.

平台差异 (有据可查):
  Windows: Pillow 原生支持 (GDI 渲染, Image.core.drawwmf), **零依赖**即可.
           Pillow 官方文档: "On Windows, it can read WMF and EMF files."
  Linux:   Pillow 无该能力 (drawwmf 仅 Windows 编译), 需外部工具
           (LibreOffice / Inkscape / emf2png — 后者为 Inkscape+JuNest 封装,
           自带白底合成, 适合无特权/不可装包环境) 作为可选增强.

设计: 逐个途径尝试, 任一成功即返回 PNG 字节; 全部不可用返回 None (上层 defer).
"""
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger("vectimg")

# 矢量扩展名 (需渲染为位图)
VECTOR_EXTS = frozenset({"emf", "wmf", "emfz", "wmz", "svg"})

# 渲染 dpi (与 PDF 扫描页一致; OCR 需足够分辨率, Pillow 默认 72 偏低)
DEFAULT_DPI = 150


def is_vector_blob(blob, ext=""):
    """是否为需渲染的矢量图 (按字节签名 + 扩展名)."""
    e = (ext or "").lower()
    if "." in e:
        e = e.rsplit(".", 1)[1]
    if e in VECTOR_EXTS:
        return True
    if not blob or len(blob) < 4:
        return False
    if blob[:4] == b"\xd7\xcd\xc6\x9a":  # WMF (placeable)
        return True
    if blob[:4] == b"\x01\x00\x00\x00" and len(blob) > 44 and blob[40:44] == b" EMF":
        return True
    if blob[:5] == b"<?xml" or blob[:4] == b"<svg":  # SVG
        return True
    return False


def _render_with_pillow(blob, ext, dpi=DEFAULT_DPI):
    """途径 1: Pillow. Windows 原生 (GDI); Linux 需已注册 handler. 失败返回 None."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return None
    # Windows 上 Pillow 导入时自动注册 WMF/EMF handler (GDI).
    # 非 Windows 无 drawwmf, Image.open 会抛异常 -> 返回 None 走下一途径.
    try:
        with Image.open(io.BytesIO(blob)) as im:
            try:
                im.load(dpi=dpi)
            except TypeError:
                im.load()  # 老版本 Pillow 无 dpi 参数
            if im.mode not in ("RGB", "RGBA", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as e:
        log.debug("Pillow 渲染矢量图失败 (%s)", e)
        return None


def _find_tool(names):
    """在 PATH 中找第一个可用命令; 返回路径或 None."""
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _run(cmd, timeout=60):
    """执行外部命令, 成功(exit 0)返回 True, 否则 False. 不抛异常."""
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return r.returncode == 0
    except Exception as e:
        log.debug("命令执行失败 %s: %s", cmd[0] if cmd else "?", e)
        return False


def _render_with_libreoffice(blob, ext, dpi=DEFAULT_DPI, timeout=120):
    """途径 2: LibreOffice headless 转 PNG. 不可用/失败返回 None."""
    so = _find_tool(["soffice", "libreoffice"])
    if not so:
        return None
    e = _ext_of(ext)
    tmp = tempfile.mkdtemp(prefix="doc2md_lo_")
    try:
        src = os.path.join(tmp, f"in.{e}")
        with open(src, "wb") as f:
            f.write(blob)
        outdir = os.path.join(tmp, "out")
        os.makedirs(outdir, exist_ok=True)
        # 独立 profile: 避免与已运行的 LO 实例冲突 (headless 并发/首次初始化)
        profile = os.path.join(tmp, "profile")
        profile_uri = "file://" + profile
        if not _run([so, f"-env:UserInstallation={profile_uri}",
                     "--headless", "--convert-to", "png",
                     "--outdir", outdir, src], timeout=timeout):
            return None
        # LO 通常输出 in.png; 但为稳健起见扫目录取任意产物
        for fn in os.listdir(outdir):
            if fn.lower().endswith(".png"):
                p = os.path.join(outdir, fn)
                if os.path.getsize(p) > 0:
                    return open(p, "rb").read()
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _render_with_inkscape(blob, ext, dpi=DEFAULT_DPI, timeout=60):
    """途径 3: Inkscape 导出 PNG. 不可用/失败返回 None."""
    ink = _find_tool(["inkscape"])
    if not ink:
        return None
    e = _ext_of(ext)
    tmp = tempfile.mkdtemp(prefix="doc2md_ink_")
    try:
        src = os.path.join(tmp, f"in.{e}")
        out = os.path.join(tmp, "out.png")
        with open(src, "wb") as f:
            f.write(blob)
        ok = _run([ink, "--export-type=png", f"--export-filename={out}",
                   f"--export-dpi={dpi}", src], timeout=timeout)
        if ok and os.path.exists(out):
            return open(out, "rb").read()
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _render_with_emf2png(blob, ext, dpi=DEFAULT_DPI, timeout=120):
    """途径 4: emf2png 封装脚本 (Inkscape + JuNest/proot, 白底 PNG).

    适用无特权/不可装包环境: emf2png 内部以 proot 隔离跑 Inkscape 导出,
    再用 ImageMagick 合成白底 (透明底会被多模态模型渲染成黑底, 故需白底).
    不可用/失败返回 None.
    """
    tool = _find_tool(["emf2png"])
    if not tool:
        return None
    e = _ext_of(ext)
    tmp = tempfile.mkdtemp(prefix="doc2md_e2p_")
    try:
        src = os.path.join(tmp, f"in.{e}")
        out = os.path.join(tmp, "out.png")
        with open(src, "wb") as f:
            f.write(blob)
        if not _run([tool, src, out, str(dpi)], timeout=timeout):
            return None
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return open(out, "rb").read()
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ext_of(ext):
    e = (ext or "").lower()
    if "." in e:
        e = e.rsplit(".", 1)[1]
    return e or "emf"


def _sniff_vector_ext(blob, ext):
    """按字节签名推断矢量真实格式, 覆盖(可能错的)扩展名.

    设计哲学一致性: "内容优于扩展名". 实际遇到 .wmf 扩展名但内容是 EMF 的文件,
    渲染器按扩展名解析会失败, 而字节签名可靠 --- 故以签名为准.
    """
    if not blob:
        return _ext_of(ext)
    if blob[:4] == b"\xd7\xcd\xc6\x9a":       # WMF (placeable)
        return "wmf"
    if blob[:4] == b"\x01\x00\x00\x00" and len(blob) > 44 and blob[40:44] == b" EMF":
        return "emf"
    if blob[:5] == b"<?xml" or blob[:4] == b"<svg":  # SVG
        return "svg"
    return _ext_of(ext)


def render_vector(blob, ext="", dpi=DEFAULT_DPI):
    """把矢量图渲染为 PNG 字节; 全部途径失败返回 None.

    途径顺序: Pillow (Windows 零依赖优先) → LibreOffice → Inkscape → emf2png.
    调用方应在返回 None 时降级 (defer 给 stage2).
    渲染前按字节签名校正扩展名 (内容优于扩展名).
    """
    if not blob:
        return None
    ext = _sniff_vector_ext(blob, ext)
    for fn in (_render_with_pillow, _render_with_libreoffice,
               _render_with_inkscape, _render_with_emf2png):
        try:
            png = fn(blob, ext, dpi)
        except Exception as e:
            log.debug("%s 渲染异常: %s", fn.__name__, e)
            png = None
        if png:
            return png
    return None


def available_renderers():
    """返回当前可用的渲染途径名列表 (供日志/诊断, 不用于决策)."""
    out = []
    try:
        from PIL import Image
        if hasattr(Image.core, "drawwmf"):
            out.append("pillow-gdi")
    except ImportError:
        pass
    if _find_tool(["soffice", "libreoffice"]):
        out.append("libreoffice")
    if _find_tool(["inkscape"]):
        out.append("inkscape")
    if _find_tool(["emf2png"]):
        out.append("emf2png")
    return out
