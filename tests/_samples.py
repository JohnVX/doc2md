"""生成各格式样本文件 (hermetic: 测试输入全靠代码生成, 无外部 fixture 依赖).

用工程已有的库(python-docx/openpyxl/python-pptx/PyMuPDF/Pillow)现场造文件.
"""
import os


def gen_docx(path):
    from docx import Document
    d = Document()
    d.add_heading("样本标题", 0)
    d.add_paragraph("样本正文段落。")
    d.save(path)


def gen_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"] = "列1", "列2"
    ws.append(["a", "b"])
    ws.append(["c", "d"])
    wb.save(path)


def gen_pptx(path):
    from pptx import Presentation
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "样本幻灯片标题"
    if len(slide.placeholders) > 1:
        slide.placeholders[1].text = "样本正文"
    prs.save(path)


def gen_pdf(path):
    import fitz
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 72), "Hello 样本 PDF")
    doc.save(path)
    doc.close()


def gen_png(path):
    from PIL import Image
    Image.new("RGB", (16, 16), (255, 0, 0)).save(path)


def gen_jpeg(path):
    from PIL import Image
    Image.new("RGB", (16, 16), (0, 255, 0)).save(path, "JPEG")


def gen_md(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 样本标题\n\n- 列表项一\n- 列表项二\n\n样本正文段落。\n")


def gen_code(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("cmake_minimum_required(VERSION 3.16)\nproject(x)\n")


def gen_txt(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("一段纯文本样本。\n")


def _load_font(size):
    """跨平台尝试加载一个 TTF, 找不到用 PIL 默认."""
    from PIL import ImageFont
    for p in ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
              "C:/Windows/Fonts/arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def gen_text_png(path, text="OCRTEST123"):
    """生成含清晰拉丁文字的图片(供 OCR 正向测试). DejaVu 含拉丁字形可被 OCR."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 80), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((10, 20), text, fill=(0, 0, 0), font=_load_font(44))
    img.save(path)


_GEN = {
    "docx": gen_docx, "xlsx": gen_xlsx, "pptx": gen_pptx, "pdf": gen_pdf,
    "png": gen_png, "jpeg": gen_jpeg, "md": gen_md, "code": gen_code, "txt": gen_txt,
}

# kind -> detector 期望识别出的 format
_EXPECTED_FORMAT = {
    "docx": "docx", "xlsx": "xlsx", "pptx": "pptx", "pdf": "pdf",
    "png": "png", "jpeg": "jpeg", "md": "md", "code": "code", "txt": "txt",
}

_PLAN = [
    ("sample.docx", "docx"), ("data.xlsx", "xlsx"), ("slides.pptx", "pptx"),
    ("doc.pdf", "pdf"), ("pic.png", "png"), ("photo.jpg", "jpeg"),
    ("readme.md", "md"), ("CMakeLists.txt", "code"), ("note.txt", "txt"),
]


def generate_corpus(dest_dir):
    """在 dest_dir 生成全套样本, 返回 {filename: expected_format}."""
    expected = {}
    for name, kind in _PLAN:
        _GEN[kind](os.path.join(dest_dir, name))
        expected[name] = _EXPECTED_FORMAT[kind]
    return expected


def generate(dest_dir, kind, name):
    """生成单个指定 kind 的样本."""
    _GEN[kind](os.path.join(dest_dir, name))
    return _EXPECTED_FORMAT[kind]
