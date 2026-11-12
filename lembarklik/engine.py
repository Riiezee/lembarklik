"""Local file processing shared by the desktop interface and smoke checks."""

from contextlib import closing, contextmanager
from io import BytesIO
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from zipfile import ZIP_STORED, ZipFile

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
import pypdfium2 as pdfium


def parse_pages(spec: str, total: int) -> list[int]:
    """Return zero-based page indices in the user's specified order."""
    if total < 1:
        raise ValueError("This PDF has no pages.")
    if not spec.strip():
        return list(range(total))

    indices = []
    seen = set()
    for entry in spec.split(","):
        match = re.fullmatch(r"\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*", entry)
        if match is None:
            raise ValueError("Use page numbers and ranges like 1, 3-5.")
        first = int(match[1])
        last = int(match[2]) if match[2] else first
        if not 1 <= first <= last <= total:
            raise ValueError(f"Choose ascending page ranges between 1 and {total}.")
        for page in range(first - 1, last):
            if page not in seen:
                indices.append(page)
                seen.add(page)
    return indices


def _reader(path: Path) -> PdfReader:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError(f"{path.name} is password protected. Choose an unencrypted PDF.")
    if not len(reader.pages):
        raise ValueError(f"{path.name} has no pages.")
    return reader


@contextmanager
def _output(path: Path):
    # Commit a complete result in the destination folder, even across drives.
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
        pending = Path(stream.name)
    try:
        yield pending
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def process(
    tool: str,
    sources: list[Path],
    destination: Path,
    *,
    pages: str = "",
    rotation: int = 90,
    password: str = "",
    dpi: int = 150,
    scan_mode: str = "Color",
) -> str:
    counts = {
        "merge": 2, "extract": 1, "rotate": 1, "optimize": 1,
        "protect": 1, "images_to_pdf": 1, "pdf_to_jpg": 1,
    }
    if tool not in counts:
        raise ValueError("Choose a PDF tool.")
    if len(sources) < counts[tool]:
        raise ValueError(f"Select at least {counts[tool]} input file(s).")
    if tool not in {"merge", "images_to_pdf"} and len(sources) != 1:
        raise ValueError("Select one input file for this tool.")

    expected_suffix = ".zip" if tool == "pdf_to_jpg" else ".pdf"
    if destination.suffix.lower() != expected_suffix:
        raise ValueError(f"Save the result with a {expected_suffix} extension.")
    if any(
        destination.resolve() == path.resolve()
        or (destination.exists() and destination.samefile(path))
        for path in sources
    ):
        raise ValueError("Choose an output filename different from the inputs.")

    if tool == "protect" and not password.strip():
        raise ValueError("Enter a password for the new PDF.")
    if tool == "protect" and len(password.encode("utf-8")) > 127:
        raise ValueError("Use a password of at most 127 UTF-8 bytes.")
    if tool == "rotate" and rotation not in {90, 180, 270}:
        raise ValueError("Choose a rotation of 90, 180 or 270 degrees.")
    if tool == "pdf_to_jpg" and dpi not in {96, 150, 200}:
        raise ValueError("Choose 96, 150 or 200 DPI.")

    source = sources[0]
    try:
        if tool == "pdf_to_jpg":
            return _export_jpg(source, destination, pages, dpi)

        writer = PdfWriter()
        if tool == "images_to_pdf":
            for path in sources:
                with Image.open(path) as original, BytesIO() as buffer:
                    with ImageOps.exif_transpose(original) as oriented:
                        with oriented.convert("RGBA") as rgba:
                            with Image.new("RGB", rgba.size, "white") as image:
                                image.paste(rgba, mask=rgba.getchannel("A"))
                                if scan_mode == "Color":
                                    image.save(buffer, "PDF", resolution=150)
                                else:
                                    with image.convert("L") as grayscale:
                                        with ImageOps.autocontrast(grayscale) as adjusted:
                                            if scan_mode == "Black and white":
                                                with adjusted.point(lambda value: 255 if value >= 168 else 0, "1") as mono:
                                                    mono.save(buffer, "PDF", resolution=150)
                                            else:
                                                adjusted.save(buffer, "PDF", resolution=150)
                    buffer.seek(0)
                    writer.append(PdfReader(buffer), import_outline=False)
        elif tool == "merge":
            for index, path in enumerate(sources, 1):
                reader = _reader(path)
                # Distinguish equally named form fields across merged inputs.
                if reader.get_fields():
                    reader.add_form_topname(f"document_{index}")
                writer.append(reader)
        elif tool == "extract":
            reader = _reader(source)
            writer.append(reader, pages=parse_pages(pages, len(reader.pages)))
        else:
            reader = _reader(source)
            writer.clone_document_from_reader(reader)
            if tool == "rotate":
                for index in parse_pages(pages, len(writer.pages)):
                    writer.pages[index].rotate(rotation)
            elif tool == "protect":
                writer.encrypt(password, algorithm="AES-256")
            elif tool == "optimize":
                for page in writer.pages:
                    page.compress_content_streams()
                writer.compress_identical_objects()

        if tool == "optimize":
            original_size = source.stat().st_size
        with _output(destination) as pending:
            writer.write(pending)
            if tool == "optimize" and pending.stat().st_size >= original_size:
                raise ValueError("Lossless optimization found no size savings. No output was written.")

        if tool == "optimize":
            savings = 100 * (1 - destination.stat().st_size / original_size)
            return f"Saved {savings:.1f}% · {destination.name}"
        return f"Saved {len(writer.pages)} page(s) · {destination.name}"
    except PdfReadError as exc:
        raise ValueError("A PDF could not be read. Check that your inputs are valid PDFs.") from exc


def _export_jpg(source: Path, destination: Path, pages: str, dpi: int) -> str:
    reader = _reader(source)
    indices = parse_pages(pages, len(reader.pages))
    with pdfium.PdfDocument(source) as document, _output(destination) as pending:
        scale = dpi / 72
        with ZipFile(pending, "w", compression=ZIP_STORED) as archive:
            for index in indices:
                with closing(document[index]) as page:
                    width, height = page.get_size()
                    if width * height * scale**2 > 40_000_000:
                        raise ValueError("A page is too large to render. Try a lower DPI.")
                    bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255))
                    try:
                        with bitmap.to_pil() as rendered, BytesIO() as jpeg:
                            with rendered.convert("RGB") as rgb:
                                rgb.save(jpeg, format="JPEG", quality=90)
                            archive.writestr(f"page-{index + 1:04d}.jpg", jpeg.getvalue())
                    finally:
                        bitmap.close()
    return f"Saved {len(indices)} JPEG(s) · {destination.name}"
