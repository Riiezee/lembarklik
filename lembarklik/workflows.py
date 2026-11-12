"""PDF editing, review and tool dispatch."""

from collections import Counter
from difflib import unified_diff
from io import BytesIO
import math
import re
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageOps
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import RectangleObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from . import converters
from .catalog import MULTI, TOOLS
from .engine import _reader, parse_pages, process
from .local_programs import office_pdf, require


BASE_TOOLS = {"merge", "extract", "rotate", "protect", "images_to_pdf", "pdf_to_jpg", "optimize"}


def validate_sources(tool, paths):
    if len(paths) < tool.minimum:
        raise ValueError(f"Choose at least {tool.minimum} input file(s).")
    if tool.key not in MULTI and len(paths) != 1:
        raise ValueError("Choose one file for this tool.")
    if any(path.suffix.lower().lstrip(".") not in tool.extensions.split(",") for path in paths):
        raise ValueError("This file type is not supported by the selected tool.")
    if tool.key == "compare" and len(paths) != 2:
        raise ValueError("Choose exactly two PDFs to compare.")
    if tool.key in {"sign", "edit"} and (paths[0].suffix.lower() != ".pdf" or len(paths) > 2
                              or (len(paths) == 2 and paths[1].suffix.lower() == ".pdf")):
        raise ValueError("Add the PDF first, followed by at most one image.")


def _writer(source):
    return PdfWriter(clone_from=_reader(source))


def _rectangles(options, count, *, multiple=False):
    entries = options.get("region") or []
    if not isinstance(entries, list) or not entries or len(entries) > (60 if multiple else 1):
        raise ValueError("Draw the required area on the page preview.")
    regions = {}
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 5:
            raise ValueError("Draw the area again.")
        page, left, top, right, bottom = entry
        if (type(page) is not int or not 1 <= page <= count
                or not all(type(x) in {int, float} and math.isfinite(x) and 0 <= x <= 1
                           for x in (left, top, right, bottom))
                or right - left < .003 or bottom - top < .003):
            raise ValueError("Draw a rectangle inside the selected page.")
        regions.setdefault(page - 1, []).append((left, top, right, bottom))
    return regions


def _visible_page(page):
    if page.rotation:
        page.transfer_rotation_to_content()
    left, bottom, right, top = map(float, page.cropbox)
    return left, bottom, right - left, top - bottom


def _box(region, width, height):
    left, top, right, bottom = region
    return left * width, (1 - bottom) * height, (right - left) * width, (bottom - top) * height


def _overlay(page, left, bottom, width, height, draw):
    with BytesIO() as data:
        canvas = Canvas(data, pagesize=(width, height))
        draw(canvas)
        canvas.showPage()
        canvas.save()
        data.seek(0)
        layer = PdfReader(data).pages[0]
        page.merge_translated_page(layer, left, bottom)


def _text_in_box(canvas, text, font, x, y, width, height):
    canvas.setFillColorRGB(0.09, 0.14, 0.23)
    size = min(96, height * .72, width * 72 / max(1, stringWidth(text, font, 72)))
    if size < 4:
        raise ValueError("Draw a larger area or shorten the text.")
    canvas.setFont(font, size)
    canvas.drawString(x, y + (height - size) / 2 + size * .18, text)


def _visual_edit(key, source, paths, output, options):
    writer = _writer(source)
    areas = _rectangles(options, len(writer.pages))
    for index, regions in areas.items():
        page = writer.pages[index]
        left, bottom, width, height = _visible_page(page)
        x, y, w, h = _box(regions[0], width, height)
        if key == "crop":
            page.cropbox = RectangleObject((left + x, bottom + y, left + x + w, bottom + y + h))
        elif (key == "sign" and len(paths) == 2) or (key == "edit" and options.get("mode") == "Image"):
            if len(paths) != 2:
                raise ValueError("Add a PNG or JPEG after selecting the PDF.")
            with Image.open(paths[1]) as original, ImageOps.exif_transpose(original) as image:
                _overlay(page, left, bottom, width, height, lambda c: c.drawImage(
                    ImageReader(image), x, y, w, h, mask="auto", preserveAspectRatio=True,
                ))
        elif key == "edit" and options.get("mode") in {"Rectangle", "Highlight"}:
            highlight = options["mode"] == "Highlight"

            def draw_shape(canvas):
                canvas.setLineWidth(1.5)
                canvas.setStrokeColorRGB(.15, .30, .88)
                canvas.setFillColorRGB(1, .88, .19, alpha=.38)
                canvas.rect(x, y, w, h, stroke=int(not highlight), fill=int(highlight))

            _overlay(page, left, bottom, width, height, draw_shape)
        else:
            value = str(options.get("text", "")).strip()
            if not value or len(value) > 250:
                raise ValueError("Enter between 1 and 250 characters.")
            font = "Times-Italic" if key == "sign" else "Helvetica"
            _overlay(page, left, bottom, width, height, lambda c: _text_in_box(c, value, font, x, y, w, h))
    writer.write(output)


def _stamp(key, source, output, options):
    writer = _writer(source)
    pages = parse_pages(options.get("pages", ""), len(writer.pages))
    if key == "numbers":
        start = int(options.get("start", 1))
        position = options.get("position", "Center")
        if not 0 <= start <= 999999 or position not in {"Left", "Center", "Right"}:
            raise ValueError("Choose a valid starting number and position.")
    else:
        text = options.get("text", "").strip()
        if not 1 <= len(text) <= 120:
            raise ValueError("Enter a watermark up to 120 characters long.")

    for sequence, index in enumerate(pages):
        page = writer.pages[index]
        left, bottom, width, height = _visible_page(page)

        def draw(canvas):
            if key == "watermark":
                canvas.translate(width / 2, height / 2)
                canvas.rotate(35)
                canvas.setFillColorRGB(.22, .30, .41, alpha=.23)
                size = min(66, width * .83 * 72 / max(1, stringWidth(text, "Helvetica-Bold", 72)))
                canvas.setFont("Helvetica-Bold", size)
                canvas.drawCentredString(0, -size / 3, text)
            else:
                number = str(start + sequence)
                canvas.setFont("Helvetica", 10)
                if position == "Left":
                    canvas.drawString(width * .05, height * .035, number)
                elif position == "Right":
                    canvas.drawRightString(width * .95, height * .035, number)
                else:
                    canvas.drawCentredString(width / 2, height * .035, number)

        _overlay(page, left, bottom, width, height, draw)
    writer.write(output)


def _raster_pdf(source, output, options, *, redact=False):
    reader = _reader(source)
    areas = _rectangles(options, len(reader.pages), multiple=True) if redact else {}
    mode = options.get("mode", "Compact (rasterized)")
    dpi, quality = (180, 90) if redact else ((120, 75) if mode == "Compact (rasterized)" else (85, 53))
    canvas = Canvas(str(output))
    with pdfium.PdfDocument(source) as document:
        for index in range(len(document)):
            with converters.render(document, index, dpi) as (image, size), BytesIO() as encoded:
                drawing = ImageDraw.Draw(image)
                for left, top, right, bottom in areas.get(index, []):
                    drawing.rectangle((math.floor(left * image.width), math.floor(top * image.height),
                                       math.ceil(right * image.width), math.ceil(bottom * image.height)),
                                      fill="black")
                image.save(encoded, "JPEG", quality=quality, optimize=True)
                encoded.seek(0)
                canvas.setPageSize(size)
                canvas.drawImage(ImageReader(encoded), 0, 0, *size)
                canvas.showPage()
    canvas.save()
    if not redact and output.stat().st_size >= source.stat().st_size:
        raise ValueError("This compression mode did not reduce the file size.")


def _split(source, output, options):
    reader = _reader(source)
    groups = options.get("groups", "").strip()
    ranges = ([parse_pages(group, len(reader.pages)) for group in groups.split(";")]
              if groups else [[page] for page in range(len(reader.pages))])
    if len(ranges) > 400 or groups and any(not group.strip() for group in groups.split(";")):
        raise ValueError("Enter up to 400 non-empty page groups.")
    with ZipFile(output, "w") as archive:
        for index, pages in enumerate(ranges, 1):
            writer = PdfWriter()
            writer.append(reader, pages=pages)
            with BytesIO() as result:
                writer.write(result)
                archive.writestr(f"part-{index:04d}.pdf", result.getvalue())


def _unlock(source, output, options):
    reader = PdfReader(source)
    if not reader.is_encrypted:
        raise ValueError("This PDF is already unencrypted.")
    if not reader.decrypt(options.get("password", "")):
        raise ValueError("The PDF password is incorrect.")
    PdfWriter(clone_from=reader).write(output)


def _forms(source, output, options):
    writer = _writer(source)
    supported = {key for key, field in (writer.get_fields() or {}).items() if field.get("/FT") in {"/Tx", "/Ch"}}
    fields = options.get("fields") or {}
    if not isinstance(fields, dict) or not fields or not fields.keys() <= supported:
        raise ValueError("Select a PDF with supported form fields and enter values.")
    if not all(isinstance(value, str) and len(value) <= 4000 for value in fields.values()):
        raise ValueError("Enter text values up to 4,000 characters per field.")
    writer.update_page_form_field_values(None, fields, auto_regenerate=False)
    writer.write(output)


def _repair(source, output):
    try:
        writer = _writer(source)
    except PdfReadError:
        # Recover page appearances when the file opens in PDFium but pypdf cannot rewrite it.
        with pdfium.PdfDocument(source) as document:
            if not len(document):
                raise ValueError("No recoverable PDF pages were found.")
            canvas = Canvas(str(output))
            for index in range(len(document)):
                with converters.render(document, index, 150) as (image, size):
                    canvas.setPageSize(size)
                    canvas.drawImage(ImageReader(image), 0, 0, *size)
                    canvas.showPage()
            canvas.save()
        return "Recovered page appearances in a flattened PDF."
    writer.write(output)
    return "Rewrote the PDF successfully."


def _compare(paths, output):
    readers = [_reader(path) for path in paths]
    texts = [[page.extract_text() or "" for page in reader.pages] for reader in readers]
    with pdfium.PdfDocument(paths[0]) as first, pdfium.PdfDocument(paths[1]) as second:
        changed = []
        for index in range(min(len(first), len(second))):
            with converters.render(first, index, 65) as (a, size_a), converters.render(second, index, 65) as (b, size_b):
                if size_a != size_b or a.size != b.size or a.tobytes() != b.tobytes():
                    changed.append(str(index + 1))
    diff = unified_diff(
        "\n\f\n".join(texts[0]).splitlines(), "\n\f\n".join(texts[1]).splitlines(),
        fromfile="First PDF", tofile="Second PDF", lineterm="",
    )
    report = [f"First PDF pages: {len(texts[0])}", f"Second PDF pages: {len(texts[1])}",
              "Changed overlapping page previews: " + (", ".join(changed) if changed else "none"),
              "", "Extracted text differences", *diff]
    output.write_text("\n".join(report) + "\n", encoding="utf-8")


def _summary(source, output, options):
    text = re.sub(r"\s+", " ", converters.document_text(source)).strip()
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part]
    count = {"Short": 3, "Standard": 6, "Detailed": 10}.get(options.get("length"), 6)
    tokens = [re.findall(r"\b[^\W\d_]{4,}\b", sentence.lower()) for sentence in sentences]
    frequency = Counter(word for words in tokens for word in words)
    ranked = sorted(range(len(sentences)), key=lambda i: -sum(frequency[word] for word in set(tokens[i]))
                    / math.sqrt(max(1, len(tokens[i]))))
    summary = [sentences[i] for i in sorted(ranked[:count])]
    output.write_text("\n\n".join(summary) + "\n", encoding="utf-8")


def execute(key, paths, output, work, options):
    tool = TOOLS[key]
    validate_sources(tool, paths)
    if tool.dependency:
        require(tool.dependency)
    source = paths[0]
    if key in BASE_TOOLS and (key != "optimize" or options.get("mode", "Lossless") == "Lossless"):
        supported = {name: options[name] for name in ("pages", "password") if name in options}
        supported.update({name: int(options[name]) for name in ("rotation", "dpi") if name in options})
        return process(key, paths, output, **supported)
    if key == "optimize":
        _raster_pdf(source, output, options)
    elif key == "split":
        _split(source, output, options)
    elif key == "remove":
        spec = options.get("pages", "").strip()
        if not spec:
            raise ValueError("Enter the pages to remove.")
        reader = _reader(source)
        removed = set(parse_pages(spec, len(reader.pages)))
        keep = [index for index in range(len(reader.pages)) if index not in removed]
        if not keep:
            raise ValueError("Keep at least one page in the PDF.")
        writer = PdfWriter()
        writer.append(reader, pages=keep)
        writer.write(output)
    elif key == "scan":
        return process("images_to_pdf", paths, output, scan_mode=options.get("mode", "Grayscale"))
    elif key == "organize":
        if not options.get("pages", "").strip():
            raise ValueError("Enter the page order, such as 3, 1-2.")
        process("extract", paths, output, pages=options["pages"])
    elif key in {"word_to_pdf", "ppt_to_pdf", "excel_to_pdf", "pdfa"}:
        office_pdf(source, output, work, archive=key == "pdfa")
    elif key == "html_to_pdf":
        converters.html_pdf(source, output, work)
    elif key == "pdf_to_word":
        converters.pdf_to_word(source, output, work, options.get("ocr", "Off"))
    elif key == "pdf_to_excel":
        converters.pdf_to_excel(source, output)
    elif key == "pdf_to_ppt":
        converters.pdf_to_ppt(source, output, options.get("mode", "Page snapshots"))
    elif key == "markdown":
        converters.markdown(source, output)
    elif key == "ocr":
        converters.searchable_pdf(source, output, work, options.get("language", "English"))
    elif key in {"crop", "edit", "sign"}:
        _visual_edit(key, source, paths, output, options)
    elif key in {"numbers", "watermark"}:
        _stamp(key, source, output, options)
    elif key == "forms":
        _forms(source, output, options)
    elif key == "redact":
        _raster_pdf(source, output, options, redact=True)
    elif key == "unlock":
        _unlock(source, output, options)
    elif key == "repair":
        return _repair(source, output)
    elif key == "compare":
        _compare(paths, output)
    elif key == "summarize":
        if options.get("mode") == "Local AI":
            length = options.get("length", "Standard").lower()
            converters.model_text(source, output, work, f"Write a {length} summary of the document. Return only the summary.")
        else:
            _summary(source, output, options)
    elif key == "translate":
        converters.translate(source, output, work, options.get("language", ""))
    return ""
