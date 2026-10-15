"""矢量图渲染测试 (vectimg).

覆盖: 矢量图识别 / 渲染途径降级 / 无渲染能力时的 defer 语义 / 扩展名保留.

注意: 真实 EMF/WMF 渲染依赖环境能力 (Windows GDI / LibreOffice / Inkscape).
本测试不假设某途径存在, 而是用 mock 验证"有渲染能力"与"无渲染能力"两条路径
的代码行为; 真实渲染由部署环境验证。
"""
import io
import os

from tests._runner import assert_eq, assert_true, cleanup, make_tmp_input, make_tmp_output

from doc2md import ocr, pipeline, vectimg

# 合成的最小 EMF (仅头, 无有效记录) —— 真实渲染器也会拒绝, 故仅用于识别测试
_SYNTH_EMF = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 100
_SYNTH_WMF = b"\xd7\xcd\xc6\x9a" + b"\x00" * 100


def _fake_png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(buf, "PNG")
    return buf.getvalue()


CASES = []


def test_is_vector_blob():
    """矢量图识别: 按字节签名与扩展名."""
    assert_true(vectimg.is_vector_blob(_SYNTH_EMF), "EMF 签名识别")
    assert_true(vectimg.is_vector_blob(_SYNTH_WMF), "WMF 签名识别")
    assert_true(vectimg.is_vector_blob(b"", "x.svg"), "SVG 扩展名识别")
    assert_true(vectimg.is_vector_blob(b"", "x.emf"), "EMF 扩展名识别")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert_true(not vectimg.is_vector_blob(png, "png"), "PNG 不是矢量图")
    assert_true(not vectimg.is_vector_blob(b"", ""), "空输入不是矢量图")


CASES.append(("is_vector_blob", test_is_vector_blob))


def test_render_falls_through_to_success():
    """渲染途径降级: 前一途径失败, 后一途径成功则返回其 PNG."""
    orig = vectimg._render_with_pillow
    orig2 = vectimg._render_with_libreoffice
    try:
        vectimg._render_with_pillow = lambda blob, ext, dpi=150: None  # 失败
        vectimg._render_with_libreoffice = lambda blob, ext, dpi=150, timeout=60: _fake_png()
        r = vectimg.render_vector(_SYNTH_EMF, "emf")
        assert_true(r is not None, "应降级到后一途径并成功")
        assert_true(r[:8] == b"\x89PNG\r\n\x1a\n", "返回的应是 PNG")
    finally:
        vectimg._render_with_pillow = orig
        vectimg._render_with_libreoffice = orig2


CASES.append(("render_falls_through_to_success", test_render_falls_through_to_success))


def test_render_none_when_all_fail():
    """所有途径失败 -> 返回 None (上层据此 defer)."""
    saved = (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
             vectimg._render_with_inkscape)
    try:
        vectimg._render_with_pillow = lambda *a, **k: None
        vectimg._render_with_libreoffice = lambda *a, **k: None
        vectimg._render_with_inkscape = lambda *a, **k: None
        assert_true(vectimg.render_vector(_SYNTH_EMF, "emf") is None,
                    "全部失败应返回 None")
    finally:
        (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
         vectimg._render_with_inkscape) = saved


CASES.append(("render_none_when_all_fail", test_render_none_when_all_fail))


def test_is_ocr_supported_tracks_renderability():
    """WMF/EMF 可 OCR 性 = 能否渲染 (不再因格式名一刀切跳过)."""
    saved = vectimg._render_with_pillow
    try:
        vectimg._render_with_pillow = lambda blob, ext, dpi=150: _fake_png()
        assert_true(ocr.is_ocr_supported(_SYNTH_EMF), "可渲染时支持 OCR")
        vectimg._render_with_pillow = lambda *a, **k: None
        assert_true(not ocr.is_ocr_supported(_SYNTH_EMF), "不可渲染时不支持")
    finally:
        vectimg._render_with_pillow = saved


CASES.append(("is_ocr_supported_tracks_renderability", test_is_ocr_supported_tracks_renderability))


def test_vector_image_parser_prefers_png_on_success():
    """矢量图渲染成功时: 存 PNG 资产 (供 stage2 直接读); 失败时保留原扩展名."""
    from doc2md.parsers import image as img_parser
    saved = vectimg._render_with_pillow
    out = make_tmp_output()
    try:
        ind = make_tmp_input({"a.emf": _SYNTH_EMF})
        # 成功路径: mock 渲染返回真 PNG
        vectimg._render_with_pillow = lambda blob, ext, dpi=150: _fake_png()
        ctx = {"output_root": out, "doc_id": "doc-x", "relpath": "a.emf"}
        img_parser.parse(os.path.join(ind, "a.emf"), ctx)
        adir = os.path.join(out, "original-doc", "doc-x_assets")
        names = os.listdir(adir)
        assert_true(any(n.endswith(".png") for n in names), "渲染成功应存 PNG")
        # 存的确是真 PNG (不是改名的矢量)
        pngf = [n for n in names if n.endswith(".png")][0]
        with open(os.path.join(adir, pngf), "rb") as f:
            assert_true(f.read(8) == b"\x89PNG\r\n\x1a\n", "存的应是真 PNG 字节")
    finally:
        vectimg._render_with_pillow = saved
        cleanup(out)


CASES.append(("vector_image_parser_prefers_png_on_success",
              test_vector_image_parser_prefers_png_on_success))


def test_vector_image_parser_keeps_ext_on_failure():
    """渲染失败时: 保留原扩展名 (不把矢量伪装成 .png)."""
    from doc2md.parsers import image as img_parser
    saved = (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
             vectimg._render_with_inkscape)
    out = make_tmp_output()
    try:
        ind = make_tmp_input({"b.emf": _SYNTH_EMF})
        vectimg._render_with_pillow = lambda *a, **k: None
        vectimg._render_with_libreoffice = lambda *a, **k: None
        vectimg._render_with_inkscape = lambda *a, **k: None
        ctx = {"output_root": out, "doc_id": "doc-y", "relpath": "b.emf"}
        img_parser.parse(os.path.join(ind, "b.emf"), ctx)
        adir = os.path.join(out, "original-doc", "doc-y_assets")
        names = os.listdir(adir)
        assert_true(all(not n.endswith(".png") for n in names),
                    "渲染失败不应产生 .png")
        assert_true(any(n.endswith(".emf") for n in names), "应保留 .emf 扩展名")
    finally:
        (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
         vectimg._render_with_inkscape) = saved
        cleanup(out)


CASES.append(("vector_image_parser_keeps_ext_on_failure",
              test_vector_image_parser_keeps_ext_on_failure))


def test_emf_detected_as_image():
    """EMF/WMF 被 detector 识别为 image handler (而非 binary/unsupported)."""
    from doc2md import detector
    ind = make_tmp_input({"real.emf": _SYNTH_EMF, "real.wmf": _SYNTH_WMF})
    try:
        d1 = detector.detect(os.path.join(ind, "real.emf"))
        assert_eq("emf", d1["format"], "EMF 格式")
        assert_eq("image", d1["handler"], "EMF 路由到 image")
        d2 = detector.detect(os.path.join(ind, "real.wmf"))
        assert_eq("wmf", d2["format"], "WMF 格式")
        assert_eq("image", d2["handler"], "WMF 路由到 image")
    finally:
        cleanup(ind)


CASES.append(("emf_detected_as_image", test_emf_detected_as_image))


def test_save_asset_renders_vector_centrally():
    """save_asset 统一渲染矢量图: 有渲染能力存 PNG, 无则保留原矢量与扩展名.

    这是关键设计: 所有 parser 的内嵌图都经 save_asset, 故渲染只需一处.
    """
    from doc2md import util
    saved = (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
             vectimg._render_with_inkscape)
    out = make_tmp_output()
    try:
        # 有渲染能力: 存 vec.png (真 PNG)
        vectimg._render_with_pillow = lambda blob, ext, dpi=150: _fake_png()
        ctx = {"output_root": out, "doc_id": "d1"}
        dest, ref = util.save_asset(_SYNTH_EMF, ctx, "emf")
        assert_eq("01_vec.png", dest, "渲染成功应存 vec.png")
        p = os.path.join(out, "original-doc", "d1_assets", dest)
        with open(p, "rb") as f:
            assert_true(f.read(8) == b"\x89PNG\r\n\x1a\n", "存的应是真 PNG")

        # 无渲染能力: 保留 .emf
        vectimg._render_with_pillow = lambda *a, **k: None
        vectimg._render_with_libreoffice = lambda *a, **k: None
        vectimg._render_with_inkscape = lambda *a, **k: None
        ctx2 = {"output_root": out, "doc_id": "d2"}
        dest2, _ = util.save_asset(_SYNTH_EMF, ctx2, "emf")
        assert_eq("01_img.emf", dest2, "渲染失败应保留原扩展名")

        # 普通图不受影响
        ctx3 = {"output_root": out, "doc_id": "d3"}
        png = _fake_png()
        dest3, _ = util.save_asset(png, ctx3, "png")
        assert_eq("01_img.png", dest3, "普通 PNG 走原样保存")
    finally:
        (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
         vectimg._render_with_inkscape) = saved
        cleanup(out)


CASES.append(("save_asset_renders_vector_centrally",
              test_save_asset_renders_vector_centrally))


def test_vec_prefix_classifies_as_diagram():
    """渲染后的 vec.png 应归类 diagram (矢量语义经文件名保留)."""
    from doc2md.parsers._common import guess_image_class
    png = _fake_png()
    assert_eq("diagram", guess_image_class(png, "01_vec.png"), "vec 前缀 -> diagram")
    assert_eq("screenshot", guess_image_class(png, "01_img.png"), "普通图 -> screenshot")


CASES.append(("vec_prefix_classifies_as_diagram", test_vec_prefix_classifies_as_diagram))
