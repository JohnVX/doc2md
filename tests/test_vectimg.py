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
             vectimg._render_with_inkscape, vectimg._render_with_emf2png)
    try:
        vectimg._render_with_pillow = lambda *a, **k: None
        vectimg._render_with_libreoffice = lambda *a, **k: None
        vectimg._render_with_inkscape = lambda *a, **k: None
        vectimg._render_with_emf2png = lambda *a, **k: None
        assert_true(vectimg.render_vector(_SYNTH_EMF, "emf") is None,
                    "全部失败应返回 None")
    finally:
        (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
         vectimg._render_with_inkscape, vectimg._render_with_emf2png) = saved


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
             vectimg._render_with_inkscape, vectimg._render_with_emf2png)
    out = make_tmp_output()
    try:
        ind = make_tmp_input({"b.emf": _SYNTH_EMF})
        vectimg._render_with_pillow = lambda *a, **k: None
        vectimg._render_with_libreoffice = lambda *a, **k: None
        vectimg._render_with_inkscape = lambda *a, **k: None
        vectimg._render_with_emf2png = lambda *a, **k: None
        ctx = {"output_root": out, "doc_id": "doc-y", "relpath": "b.emf"}
        img_parser.parse(os.path.join(ind, "b.emf"), ctx)
        adir = os.path.join(out, "original-doc", "doc-y_assets")
        names = os.listdir(adir)
        assert_true(all(not n.endswith(".png") for n in names),
                    "渲染失败不应产生 .png")
        assert_true(any(n.endswith(".emf") for n in names), "应保留 .emf 扩展名")
    finally:
        (vectimg._render_with_pillow, vectimg._render_with_libreoffice,
         vectimg._render_with_inkscape, vectimg._render_with_emf2png) = saved
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
             vectimg._render_with_inkscape, vectimg._render_with_emf2png)
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
        vectimg._render_with_emf2png = lambda *a, **k: None
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
         vectimg._render_with_inkscape, vectimg._render_with_emf2png) = saved
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


def test_emf2png_path_uses_tool_and_reads_output():
    """emf2png 途径: 找到工具则调用并读取其输出 PNG; 找不到工具返回 None.

    不依赖真实 emf2png (mock _find_tool/_run), 任何环境可跑.
    """
    import tempfile
    saved_find, saved_run = vectimg._find_tool, vectimg._run
    try:
        # 无 emf2png 工具 -> None
        vectimg._find_tool = lambda names: None
        assert_true(vectimg._render_with_emf2png(_SYNTH_EMF, "emf") is None,
                    "无工具应返回 None")

        # 有工具且执行成功 -> 读取输出 PNG
        def fake_run(cmd, timeout=60):
            # cmd = [tool, src, out, dpi]; 写出 PNG 到 out
            with open(cmd[2], "wb") as f:
                f.write(_fake_png())
            return True
        vectimg._find_tool = lambda names: "/fake/emf2png"
        vectimg._run = fake_run
        r = vectimg._render_with_emf2png(_SYNTH_EMF, "emf")
        assert_true(r is not None and r[:8] == b"\x89PNG\r\n\x1a\n",
                    "应返回工具产出的 PNG")

        # 工具执行失败 -> None
        vectimg._run = lambda cmd, timeout=60: False
        assert_true(vectimg._render_with_emf2png(_SYNTH_EMF, "emf") is None,
                    "命令失败应返回 None")
    finally:
        vectimg._find_tool, vectimg._run = saved_find, saved_run


CASES.append(("emf2png_path_uses_tool_and_reads_output",
              test_emf2png_path_uses_tool_and_reads_output))


def test_available_renderers_reports_emf2png():
    """available_renderers 应报告 emf2png (存在时)."""
    saved_find = vectimg._find_tool
    try:
        vectimg._find_tool = lambda names: ("/x/emf2png" if "emf2png" in names else None)
        assert_true("emf2png" in vectimg.available_renderers(),
                    "应报告 emf2png 可用")
    finally:
        vectimg._find_tool = saved_find


CASES.append(("available_renderers_reports_emf2png",
              test_available_renderers_reports_emf2png))


def test_sniff_ext_overrides_wrong_extension():
    """按字节签名校正扩展名: .wmf 扩展名但内容为 EMF, 渲染前按 emf 处理.

    动机: 实测遇到扩展名为 .wmf 实为 EMF 的文件, 按扩展名解析会失败;
    内容签名可靠 -> 以签名为准 (与 is_vector_blob 的判据一致).
    """
    # 内容为 EMF, 却传 ext="wmf"
    assert_eq("emf", vectimg._sniff_vector_ext(_SYNTH_EMF, "wmf"),
              "EMF 签名应覆盖 .wmf 扩展名")
    # 内容为 WMF, 传 ext="emf"
    assert_eq("wmf", vectimg._sniff_vector_ext(_SYNTH_WMF, "emf"),
              "WMF 签名应覆盖 .emf 扩展名")
    # 签名未知时回退扩展名
    assert_eq("svg", vectimg._sniff_vector_ext(b"unknown", "svg"),
              "签名未知回退扩展名")
    assert_eq("emf", vectimg._sniff_vector_ext(b"", ""),
              "空输入回退默认 emf")


CASES.append(("sniff_ext_overrides_wrong_extension",
              test_sniff_ext_overrides_wrong_extension))


def test_normalize_png_flattens_transparency_to_white():
    """归一化: RGBA/LA/P 透明底 -> 白底非透明 RGB; RGB 幂等."""
    import io as _io
    from PIL import Image
    # RGBA 透明背景 -> 压白底
    im = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    im.putpixel((4, 4), (255, 0, 0, 255))
    buf = _io.BytesIO(); im.save(buf, "PNG")
    out = vectimg._normalize_png(buf.getvalue())
    o = Image.open(_io.BytesIO(out))
    assert_eq("RGB", o.mode, "应转为 RGB")
    assert_eq((255, 255, 255), o.getpixel((0, 0)), "透明区应变白")

    # 已是 RGB -> 幂等 (仍 RGB, 尺寸不变)
    im2 = Image.new("RGB", (5, 6), (10, 20, 30))
    buf2 = _io.BytesIO(); im2.save(buf2, "PNG")
    out2 = vectimg._normalize_png(buf2.getvalue())
    o2 = Image.open(_io.BytesIO(out2))
    assert_eq(("RGB", (5, 6)), (o2.mode, o2.size), "RGB 应幂等保留")


CASES.append(("normalize_png_flattens_transparency_to_white",
              test_normalize_png_flattens_transparency_to_white))


def test_normalize_png_failsafe_returns_original():
    """归一化失败安全: 无法解析的字节原样返回 (宁带 alpha 也不丢图)."""
    bad = b"\x89PNG-not-really"
    assert_true(vectimg._normalize_png(bad) == bad, "损坏数据应原样返回")
    assert_true(vectimg._normalize_png(None) is None, "None 应返回 None")
    assert_true(vectimg._normalize_png(b"") == b"", "空应返回空")


CASES.append(("normalize_png_failsafe_returns_original",
              test_normalize_png_failsafe_returns_original))


def test_render_vector_normalizes_any_renderer_output():
    """render_vector 出口统一归一化: 即使途径返回透明 PNG, 出口也是白底 RGB.

    这是"一致可靠"的关键: 不依赖各途径自身是否压白底.
    """
    import io as _io
    from PIL import Image
    saved = vectimg._render_with_pillow
    try:
        # mock 一个"透明底"渲染器 (模拟 Inkscape/LibreOffice)
        def rgba_png(*a, **k):
            im = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
            buf = _io.BytesIO(); im.save(buf, "PNG")
            return buf.getvalue()
        vectimg._render_with_pillow = rgba_png
        r = vectimg.render_vector(_SYNTH_EMF, "emf")
        o = Image.open(_io.BytesIO(r))
        assert_eq("RGB", o.mode, "出口应归一化为 RGB")
        assert_eq((255, 255, 255), o.getpixel((0, 0)), "出口应为白底")
    finally:
        vectimg._render_with_pillow = saved


CASES.append(("render_vector_normalizes_any_renderer_output",
              test_render_vector_normalizes_any_renderer_output))
