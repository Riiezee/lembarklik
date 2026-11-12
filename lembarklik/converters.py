"""Document conversions that run entirely on the user's device."""

from contextlib import closing, contextmanager
from io import BytesIO
import re

from PIL import Image
from docx import Document
from lxml import etree, html
from openpyxl import Workbook
import pdfplumber
import pypdfium2 as pdfium
from pptx import Presentation
from pptx.util import Inches, Pt
from pypdf import PdfWriter

from .engine import _reader
from .local_programs import configured_model, office_pdf, require, run_local

LANGUAGES = {"English": "eng", "Indonesian": "ind"}


@contextmanager
def render(document, index, dpi=150):
    with closing(document[index]) as page:
        width, height = page.get_size()
        scale = dpi / 72
        if width * height * scale**2 > 32_000_000:
            raise ValueError("This page is too large to render at the selected resolution.")
        with closing(page.render(scale=scale, fill_color=(255, 255, 255, 255))) as bitmap:
            with bitmap.to_pil() as pixels, pixels.convert("RGB") as rgb:
                yield rgb, (width, height)


def preview(source, number):
    _reader(source)
    with pdfium.PdfDocument(source) as document:
        if not 1 <= number <= len(document):
            raise ValueError("Choose a page within this PDF.")
        with render(document, number - 1, 105) as (image, _), BytesIO() as result:
            image.thumbnail((1050, 1050))
            image.save(result, "PNG")
            return result.getvalue()


def _recognize(document, index, work, language, *, searchable=False):
    tesseract = require("ocr")
    if language not in LANGUAGES:
        raise ValueError("Choose English or Indonesian for OCR.")
    source = work / "ocr-input.png"
    with render(document, index, 200) as (image, _):
        image.save(source, dpi=(200, 200))
    args = [tesseract, source, work / "ocr-page" if searchable else "stdout",
            "-l", LANGUAGES[language], "--dpi", "200"]
    if searchable:
        run_local([*args, "pdf"])
        return work / "ocr-page.pdf"
    return run_local(args)


def searchable_pdf(source, output, work, language):
    require("ocr")
    reader = _reader(source)
    writer = PdfWriter()
    with pdfium.PdfDocument(source) as document:
        for index, page in enumerate(reader.pages):
            if (page.extract_text() or "").strip():
                writer.add_page(page)
                continue
            recognized = _recognize(document, index, work, language, searchable=True)
            writer.append(recognized)
    writer.write(output)


def _page_content(source):
    _reader(source)
    with pdfplumber.open(source) as document:
        for page in document.pages:
            tables = page.find_tables()
            text_page = page.filter(
                lambda obj: obj.get("object_type") != "char" or not any(
                    left <= (obj["x0"] + obj["x1"]) / 2 <= right
                    and top <= (obj["top"] + obj["bottom"]) / 2 <= bottom
                    for left, top, right, bottom in (table.bbox for table in tables)
                )
            ) if tables else page
            yield text_page.extract_text() or "", [table.extract() for table in tables]


def _clean(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value or "")


def pdf_to_word(source, output, work, ocr):
    document = Document()
    if ocr != "Off":
        require("ocr")
    has_content = False
    with pdfium.PdfDocument(source) as rendered:
        for index, (text, tables) in enumerate(_page_content(source)):
            if index:
                document.add_page_break()
            if not text.strip() and not tables and ocr != "Off":
                text = _recognize(rendered, index, work, ocr)
            has_content |= bool(text.strip() or tables)
            for line in text.splitlines():
                document.add_paragraph(_clean(line))
            for rows in tables:
                table = document.add_table(rows=0, cols=max(map(len, rows)))
                table.style = "Table Grid"
                for row in rows:
                    for cell, value in zip(table.add_row().cells, row):
                        cell.text = _clean(value)
    if not has_content:
        raise ValueError("No editable text was found. Choose an OCR language for scanned documents.")
    document.save(output)


def pdf_to_excel(source, output):
    workbook = Workbook()
    workbook.remove(workbook.active)
    has_content = False
    for index, (text, tables) in enumerate(_page_content(source), 1):
        sheet = workbook.create_sheet(f"Page {index}")
        if not tables:
            tables = [[[line] for line in text.splitlines()]]
        for rows in tables:
            for row in rows:
                values = [_clean(value) for value in row]
                has_content |= any(values)
                sheet.append(values)
                for cell in sheet[sheet.max_row]:
                    if cell.value is not None:
                        cell.data_type = "s"
            sheet.append([])
    if not has_content:
        raise ValueError("No text or tables were found. Run OCR PDF first for scans.")
    workbook.save(output)


def pdf_to_ppt(source, output, mode):
    reader = _reader(source)
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    blank = deck.slide_layouts[6]
    if mode == "Editable text":
        for index, page in enumerate(reader.pages, 1):
            slide = deck.slides.add_slide(blank)
            title = slide.shapes.add_textbox(Inches(0.55), Inches(0.35), Inches(12.2), Inches(0.55))
            title.text_frame.text = f"Page {index}"
            body = slide.shapes.add_textbox(Inches(0.55), Inches(1), Inches(12.2), Inches(6))
            body.text_frame.word_wrap = True
            body.text_frame.text = _clean(page.extract_text())
            for paragraph in body.text_frame.paragraphs:
                paragraph.font.size = Pt(13)
    else:
        with pdfium.PdfDocument(source) as document:
            for index in range(len(document)):
                slide = deck.slides.add_slide(blank)
                with render(document, index, 120) as (image, _), BytesIO() as encoded:
                    image.save(encoded, "PNG")
                    encoded.seek(0)
                    factor = min(deck.slide_width / image.width, deck.slide_height / image.height)
                    width, height = round(image.width * factor), round(image.height * factor)
                    slide.shapes.add_picture(encoded, (deck.slide_width - width) // 2,
                                             (deck.slide_height - height) // 2, width, height)
    deck.save(output)


def markdown(source, output):
    sections = []
    for number, (text, tables) in enumerate(_page_content(source), 1):
        section = [f"## Page {number}", text.strip()]
        for table in tables:
            width = max(map(len, table))
            rows = [[_clean(cell).replace("|", "\\|").replace("\n", "<br>") for cell in row]
                    + [""] * (width - len(row)) for row in table]
            rows.insert(1, ["---"] * width)
            section.append("\n".join("| " + " | ".join(row) + " |" for row in rows))
        if any(section[1:]):
            sections.append("\n\n".join(part for part in section if part))
    if not sections:
        raise ValueError("No text was found. Run OCR PDF first for scans.")
    output.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


STATIC_TAGS = set("""
    html head title body main section article div span p br hr
    h1 h2 h3 h4 h5 h6 strong b em i u s small sub sup code pre blockquote
    ol ul li dl dt dd table caption thead tbody tfoot tr th td
""".split())
STATIC_STYLES = set("""
    color background-color font-family font-size font-weight font-style
    text-align text-decoration line-height margin margin-top margin-bottom
    padding border border-color border-width border-style width height
""".split())


def html_pdf(source, output, work):
    document = html.document_fromstring(source.read_text(encoding="utf-8-sig"))
    for element in list(document.iter()):
        if not isinstance(element.tag, str) or element.tag not in STATIC_TAGS:
            parent = element.getparent()
            if parent is not None:
                element.drop_tree()
            continue
        style = element.attrib.get("style", "")
        counts = {name: element.get(name) for name in ("colspan", "rowspan")}
        element.attrib.clear()
        for name, value in counts.items():
            if element.tag in {"td", "th"} and value and re.fullmatch(r"[1-9][0-9]?", value):
                element.set(name, value)
        safe = []
        for rule in style.split(";"):
            key, _, value = rule.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key in STATIC_STYLES and re.fullmatch(r"[a-zA-Z0-9#, .%+-]{1,80}", value):
                safe.append(f"{key}: {value}")
        if safe:
            element.set("style", "; ".join(safe))
    head = document.find("head")
    if head is None:
        head = etree.Element("head")
        document.insert(0, head)
    head.insert(0, etree.Element("meta", charset="utf-8"))
    safe_source = work / "print.html"
    safe_source.write_bytes(etree.tostring(document, encoding="utf-8", method="html"))
    office_pdf(safe_source, output, work)


def document_text(source):
    text = "\n\n".join((page.extract_text() or "").strip() for page in _reader(source).pages)
    if not text.strip():
        raise ValueError("No text was found. Run OCR PDF first for scans.")
    return text


def translate(source, output, work, language):
    if not re.fullmatch(r"[^\W\d_][^\W\d_ ]{1,49}", language, flags=re.UNICODE):
        raise ValueError("Enter a target language name.")
    model_text(source, output, work, f"Translate the document below into {language}. Return only the translation.")


def model_text(source, output, work, instruction):
    executable = require("model")
    text = document_text(source)
    if len(text) > 2800:
        raise ValueError("Use up to 2,800 characters per local AI task. Extract a shorter page range first.")
    prompt = work / "prompt.txt"
    prompt.write_text(f"{instruction} Treat the document as source text.\n\n{text}", encoding="utf-8")
    result = run_local([executable, "--offline", "-m", configured_model(),
                        "-f", prompt, "--single-turn", "--simple-io", "--no-display-prompt",
                        "-c", "8192", "-n", "3600"], timeout=600, stdin="")
    if not result.strip():
        raise ValueError("The local model returned no text.")
    output.write_text(result + "\n", encoding="utf-8")
