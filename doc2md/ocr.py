"""OCR 封装: rapidocr-onnxruntime 单例 + import 检测 + 无引擎时兜底 defer.

通用, 不针对任何具体图片内容. img 可为路径或 bytes.
"""
import logging
import threading

log = logging.getLogger("ocr")

_engine = None
_lock = threading.Lock()
_probed = None  # None=未探测 True/False
_broken = False  # 引擎初始化失败后置 True, 后续不再重试


def available():
    """探测本环境是否有可用 OCR 引擎(且未损坏)."""
    global _probed
    if _probed is None:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            _probed = True
        except Exception as e:  # pragma: no cover
            log.warning("rapidocr 不可用, 图片将 defer 到 stage2: %s", e)
            _probed = False
    return _probed and not _broken


def _engine_obj():
    global _engine, _broken
    if _broken:
        return None
    if _engine is None:
        with _lock:
            if _engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _engine = RapidOCR()
                    log.info("rapidocr 引擎已就绪")
                except Exception as e:
                    log.warning("rapidocr 引擎初始化失败, 后续图片 defer 到 stage2: %s", e)
                    _broken = True
                    return None
    return _engine


def ocr_image(img, ctx=None):
    """对图片 OCR, 返回按块顺序拼接的纯文本(每块一行). 无文本/无引擎返回 ''.

    img: 文件路径(str) 或图片字节(bytes).
    """
    if not available():
        return ""
    try:
        eng = _engine_obj()
        if eng is None:
            return ""
        result, _elapse = eng(img)
    except Exception as e:
        log.warning("OCR 失败 (%r): %s", img if isinstance(img, str) else "<bytes>", e)
        return ""
    if not result:
        return ""
    lines = []
    for item in result:
        # rapidocr 每项: [box, text, score]
        if item and len(item) >= 2 and item[1]:
            t = item[1].strip()
            if t:
                lines.append(t)
    return "\n".join(lines)


def ocr_or_defer(img, ctx=None, ref_path=None):
    """有引擎则 OCR 返回文本; 无引擎返回 defer 占位标记."""
    txt = ocr_image(img, ctx)
    if txt:
        return txt
    return ""  # 调用方决定如何写 defer 占位
