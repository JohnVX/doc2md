"""Windows 微诊断: _intra_op_single patch 是否生效?

在 Windows 上跑: python tests/diag_win_patch.py
直接回答: patch 后 ort.InferenceSession 是否被替换? 真实 rapidocr session 的 intra_op 是多少?
"""
import os
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")

print("=" * 70)
print("A. 基础检查")
print("=" * 70)
import onnxruntime as ort
print("onnxruntime:", ort.__version__)
print("ort.InferenceSession type:", type(ort.InferenceSession))
print("是类吗:", isinstance(ort.InferenceSession, type))

print("=" * 70)
print("B. 直接调用 doc2md 的 _intra_op_single, 看是否成功")
print("=" * 70)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from doc2md import ocr

before = ort.InferenceSession
ocr._intra_op_single()
after = ort.InferenceSession
print("patch 前:", before)
print("patch 后:", after)
print("被替换:", before is not after)
print("_d2m_patched:", getattr(after, "_d2m_patched", "<无>"))

print("=" * 70)
print("C. 用 patched 类创建一个 session, 看 intra_op")
print("=" * 70)
# 找模型
from pathlib import Path
try:
    import rapidocr_onnxruntime as r
    mp = Path(r.__file__).parent / "models" / "ch_PP-OCRv4_rec_infer.onnx"
except Exception as e:
    print("找不到 rapidocr:", e)
    mp = None
if mp and mp.exists():
    # 模拟 rapidocr: 自己传 sess_options (不设 intra_op)
    caller_opts = ort.SessionOptions()
    print("调用方 opts.intra_op (创建时):", caller_opts.intra_op_num_threads)
    try:
        s = ort.InferenceSession(str(mp), sess_options=caller_opts,
                                 providers=["CPUExecutionProvider"])
        so = s.get_session_options()
        print("session 实际 intra_op:", so.intra_op_num_threads)
        print("调用方 opts 被改为:", caller_opts.intra_op_num_threads)
    except Exception as e:
        print("创建 session 失败:", type(e).__name__, e)

print("=" * 70)
print("D. 走 doc2md 真实引擎, 检查 session intra_op")
print("=" * 70)
try:
    # 复位状态, 强制重新 init
    ocr._engine = None
    ocr._broken = False
    eng = ocr._engine_obj()
    print("引擎:", eng)
    for name, sub in [("det", eng.text_det.infer.session),
                      ("rec", getattr(eng.text_rec, "session", None))]:
        try:
            so = sub.get_session_options()
            print("  %s intra_op = %d, inter_op = %d" %
                  (name, so.intra_op_num_threads, so.inter_op_num_threads))
        except Exception as e:
            print("  %s 检查失败: %s" % (name, e))
except Exception as e:
    print("引擎初始化失败:", type(e).__name__, e)

print("=" * 70)
print("完成. 请贴回完整输出.")
print("=" * 70)
