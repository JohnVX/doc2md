"""OCR 封装: rapidocr-onnxruntime 单例 + import 检测 + 无引擎时兜底 defer.

通用, 不针对任何具体图片内容. img 可为路径或 bytes.

防超订: 构造 RapidOCR 时传 intra_op_num_threads=N (默认 1, 环境变量
DOC2MD_OCR_INTRA_OP 可调), 并 monkey-patch InferenceSession 兜底; 同时
用 cv2.setNumThreads(1) 限制 OpenCV 预处理线程 (intra_op 管不到).
不限制时 ORT 默认用全部核心, OpenCV 每个并行算子也开全核, 与 pipeline 的
ThreadPool 相乘会严重超订 (12 线程 x 全核), 并行退化为准串行.
OMP_NUM_THREADS=1 在 Windows 无效, 必须改 session 选项 /
cv2.setNumThreads. 详见 _intra_op_value / _new_engine / _limit_cv_threads.

并行由 pipeline 的 ThreadPool 提供 (ONNX run() 线程安全, 共享单实例引擎).

已探明不再优化的项 (诊断结论, 避免重复返工):
  - 单图拆解: rec(识别) 70% / det(检测) 29% / Python 胶水 0.1%. rec 单独并发达 8.73x,
    但 det-only 仅 ~2.6x -> det 是木桶短板. det 内部 infer(卷积) 占 99%, 非 Python/GIL.
  - det 模型约 400 算子, 无单一慢操作可重写, ORT 图优化已最高级. 降 limit_side_len
    可提速但必丢小字 (与精度优先冲突); 且 det 仅占 29%, 上限 ~+29% 非数量级收益.
  - 跨图片调大 rec_batch_num: 实测反而慢 2 倍多 (批越大越易混入超宽框,
    整批被 pad 到 max_wh_ratio 宽度). 故未采纳.
"""
import logging
import os
import threading
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")

log = logging.getLogger("ocr")

_engine = None
_lock = threading.Lock()
_probed = None  # None=未探测 True/False
_broken = False  # 引擎初始化失败后置 True, 后续不再重试
_ocr_seconds = 0.0  # 累计 OCR 纯耗时 (秒, 多线程下为各调用之和)
_ocr_count = 0  # 累计 OCR 次数
_ocr_first_ts = None  # 首次 OCR 开始时刻 (用于墙钟跨度)
_ocr_last_ts = None  # 最后一次 OCR 结束时刻


def reset_ocr_stats():
    """清零累计 OCR 统计 (每轮 run 开始时调用)."""
    global _ocr_seconds, _ocr_count, _ocr_first_ts, _ocr_last_ts
    with _lock:
        _ocr_seconds = 0.0
        _ocr_count = 0
        _ocr_first_ts = None
        _ocr_last_ts = None


def ocr_stats():
    """返回 (累计纯耗时秒, 次数, 墙钟跨度秒).

    墙钟跨度 = 最后一次 OCR 结束 - 首次 OCR 开始; 多线程时反映真实日历时间.
    """
    with _lock:
        span = 0.0
        if _ocr_first_ts is not None and _ocr_last_ts is not None:
            span = _ocr_last_ts - _ocr_first_ts
        return _ocr_seconds, _ocr_count, span


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


def _limit_cv_threads():
    """限制 OpenCV 内部线程池为 1 (防与 pipeline 的 OCR ThreadPool 相乘超订).

    RapidOCR 的预处理 (cv2.resize/cvtColor/warpAffine 等, 见 rapidocr
    utils/process_img.py, load_image.py) 由 OpenCV 执行; OpenCV 默认为每个
    并行算子开 CPU 核数 个内部线程. intra_op_num_threads 只管 ONNX 推理,
    **不覆盖**这些预处理算子. 于是 N 个 OCR 线程 x 每算子全核 = 严重超订,
    线程争抢导致并行退化为准串行 (与 ONNX 侧同一道理).

    与 ONNX 侧同理: 并行度应由 pipeline 的 ThreadPool 统一提供, OpenCV
    内部不再各自展开. Windows 上 OMP_NUM_THREADS 对 OpenCV 无效, 必须调
    cv2.setNumThreads. 失败静默 (纯性能优化, 不影响正确性).
    """
    try:
        import cv2
        cv2.setNumThreads(1)
    except Exception:
        pass


def _intra_op_single():
    """限制 ONNX Runtime 每次推理用 1 线程 (Windows 上 OMP_NUM_THREADS 无效).

    monkey-patch ort.InferenceSession, 强制注入 sess_options
    (intra_op_num_threads=1, inter_op_num_threads=1).

    注意: 这是**备用**机制. 首选在构造 RapidOCR 时直接传
    intra_op_num_threads=1 (见 _engine_obj), 因为 rapidocr 的 config 会读该值
    并写进自己的 sess_options, 最可靠. 本 patch 作为双保险 (防旧版 rapidocr
    或其它调用方不传 config 的情况).
    """
    try:
        import onnxruntime as ort
        if getattr(ort.InferenceSession, "_d2m_patched", False):
            return
        _orig = ort.InferenceSession

        class _SingleThread(_orig):
            _d2m_patched = True

            def __init__(self, *args, **kwargs):
                # 始终以调用方(或新建)的 sess_options 为基准, 限制 op 线程
                opts = kwargs.get("sess_options") or ort.SessionOptions()
                n = _intra_op_value()
                opts.intra_op_num_threads = n
                opts.inter_op_num_threads = 1
                kwargs["sess_options"] = opts
                super().__init__(*args, **kwargs)

        ort.InferenceSession = _SingleThread
    except Exception:
        pass


# OCR 单个推理 session 的 intra_op 线程数.
#
# 关键规律: 决定速度的是**总并发度 = OCR线程数 x intra_op** (而非单独任一):
# 只要乘积落在 12~16, 无论怎么拆都是 ~91-92s. 实测 (Windows 28核, 156 图):
#   乘积  4 -> 166s | 8 -> 107s | 12~16 -> 91~92s (最优平台) | 21 -> 101s
#   24 -> 112s | 32 -> 102~166s | 48 -> 115s
# 乘积 <12 时并行不足, >16 时超订变慢, 峰值约 19.3 核 (整机 ≤79% 上限内).
# 同乘积的等价性: 6x2=12 -> 91.9s; 12x1=12 -> 91.8s (无差异).
# 故 intra_op 本身不加速, 只是“用另一种方式凑并发”; 取 1 最省内存、最可预测,
# 并发全部由 pipeline 的 OCR 线程数提供. 环境变量 DOC2MD_OCR_INTRA_OP 可调优.
def _intra_op_value():
    try:
        v = int(os.environ.get("DOC2MD_OCR_INTRA_OP", "1"))
    except ValueError:
        v = 1
    return v


def _new_engine():
    """构造 RapidOCR 引擎, 限制 intra_op/inter_op (防超订).

    首选路径: RapidOCR(intra_op_num_threads=N) —— rapidocr 的 OrtInferSession
    会读该 config 值并写入 sess_options (见 _init_sess_opts, 值 -1 时不设置).
    这是最可靠的限制方式, 不依赖 monkey-patch.
    失败则回退到无参构造 (此时 _intra_op_single 的 patch 兜底).
    """
    from rapidocr_onnxruntime import RapidOCR
    n = _intra_op_value()
    try:
        return RapidOCR(intra_op_num_threads=n, inter_op_num_threads=1)
    except Exception:
        # 旧版/不支持 kwarg 的 rapidocr: 退回补丁路径
        return RapidOCR()


def _engine_obj():
    """惰性初始化并返回 OCR 引擎单例; 失败缓存 _broken 不再重试."""
    global _engine, _broken
    if _broken:
        return None
    if _engine is None:
        with _lock:
            if _engine is None:
                _intra_op_single()
                _limit_cv_threads()
                try:
                    _engine = _new_engine()
                    log.info("rapidocr 引擎已就绪")
                except Exception as e:
                    log.warning("rapidocr 引擎初始化失败, 后续图片 defer 到 stage2: %s", e)
                    _broken = True
                    return None
    return _engine


def is_ocr_supported(blob):
    """该图能否被 OCR 引擎读取.

    WMF/EMF 等矢量图: 能否处理取决于环境能否渲染 (Windows Pillow GDI 原生;
    Linux 需 LibreOffice/Inkscape). 不再一刀切跳过 —— 那会在 Windows 上
    误杀本可处理的图 (平台不对称缺陷). 用 vectimg.render_vector 实测。
    """
    if not blob or len(blob) < 4:
        return False
    from . import vectimg
    if vectimg.is_vector_blob(blob):
        return vectimg.render_vector(blob) is not None
    return True


def _prepare_for_ocr(img):
    """把输入规整为 OCR 引擎可读的形态; 不可读返回 None.

    矢量图 (EMF/WMF/SVG) 渲染为 PNG 字节; 其余原样返回.
    img: 文件路径(str) 或字节(bytes).
    """
    from . import vectimg
    if isinstance(img, str):
        try:
            with open(img, "rb") as f:
                blob = f.read()
        except Exception:
            return img  # 交给引擎报错
    else:
        blob = img
    if not vectimg.is_vector_blob(blob):
        return img
    ext = img.rsplit(".", 1)[-1] if isinstance(img, str) and "." in img else ""
    png = vectimg.render_vector(blob, ext)
    if png is None:
        log.info("矢量图无法渲染 (无 Windows GDI / LibreOffice / Inkscape): %s",
                 img if isinstance(img, str) else "<bytes>")
        return None
    return png


def ocr_image(img, ctx=None):
    """对图片 OCR, 返回按块顺序拼接的纯文本(每块一行). 无文本/无引擎返回 ''.

    img: 文件路径(str) 或图片字节(bytes).

    注意: 不做基于长度的"噪音"过滤: 结果长度与是否噪音无因果关系
    (真噪音 G/ooy/() 与真标签 VM/AI/def/Sun 长度完全重叠). 按长度丢结果
    会误删真实短标签, 违反"精度优先"原则; 零碎伪影保留在正文, 由 stage2
    多模态模型结合图片上下文区分.
    """
    # 矢量图 (EMF/WMF/SVG): OCR 引擎读不了, 先渲染为 PNG 字节再送入.
    # 无法渲染 (无 Windows GDI / 无 LibreOffice/Inkscape) 时返回 '', 上层 defer.
    img = _prepare_for_ocr(img)
    if img is None:
        return ""
    if not available():
        return ""
    t0 = time.time()
    try:
        eng = _engine_obj()
        if eng is None:
            return ""
        result, _elapse = eng(img)
    except Exception as e:
        log.warning("OCR 失败 (%r): %s", img if isinstance(img, str) else "<bytes>", e)
        return ""
    finally:
        global _ocr_seconds, _ocr_count, _ocr_first_ts, _ocr_last_ts
        now = time.time()
        dt = now - t0
        with _lock:
            _ocr_seconds += dt
            _ocr_count += 1
            if _ocr_first_ts is None:
                _ocr_first_ts = t0
            _ocr_last_ts = now
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
