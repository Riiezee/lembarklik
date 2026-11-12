"""Serve the desktop interface on a private loopback address."""

import argparse
import base64
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory
import threading
import webbrowser

import pypdfium2 as pdfium
from pypdf.errors import PyPdfError

from .catalog import TOOLS, WEB
from .converters import preview
from .engine import _reader
from .local_programs import HELP, programs
from .workflows import execute

LIMIT = 80 * 1024 * 1024
WORKER = threading.Lock()
ASSETS = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/tools.json": ("tools.json", "application/json; charset=utf-8"),
    "/id.json": ("id.json", "application/json; charset=utf-8"),
    "/logo.svg": ("logo.svg", "image/svg+xml"),
}
CONTENT_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


INDONESIAN = json.loads((WEB / "id.json").read_text(encoding="utf-8"))
ERROR_TEMPLATES = (
    ("Choose at least {count} input file(s).", r"(?P<count>\d+)"),
    ("Select at least {count} input file(s).", r"(?P<count>\d+)"),
    ("Choose ascending page ranges between 1 and {total}.", r"(?P<total>\d+)"),
    ("Choose a page between 1 and {total}.", r"(?P<total>\d+)"),
    ("Save the result with a {extension} extension.", r"(?P<extension>\.[a-z]+)"),
    ("{file} is password protected. Choose an unencrypted PDF.", r"(?P<file>[^\r\n]+)"),
    ("{file} has no pages.", r"(?P<file>[^\r\n]+)"),
)
ERROR_PATTERNS = tuple(
    (re.compile(re.sub(r"\\\{\w+\\\}", lambda _: capture, re.escape(template))), INDONESIAN[template])
    for template, capture in ERROR_TEMPLATES
)


def error_message(message, language):
    if language != "id":
        return message
    if message in INDONESIAN:
        return INDONESIAN[message]
    for pattern, translation in ERROR_PATTERNS:
        if match := pattern.fullmatch(message):
            return translation.format(**match.groupdict())
    return INDONESIAN["Check the selected files and options."]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _reply(self, status, mime, body, *, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CONTENT_POLICY)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        if "error" in data:
            data = {"error": error_message(data["error"], self.headers.get("Accept-Language"))}
        self._reply(status, "application/json; charset=utf-8", json.dumps(data).encode("utf-8"))

    def _valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self._valid_host():
            self._json(403, {"error": "Use the LembarKlik address printed by the launcher."})
        elif self.path == "/":
            markup = (WEB / "index.html").read_text(encoding="utf-8").replace("__LOCAL_TOKEN__", self.server.token)
            self._reply(200, "text/html; charset=utf-8", markup.encode("utf-8"))
        elif self.path in ASSETS:
            name, mime = ASSETS[self.path]
            self._reply(200, mime, (WEB / name).read_bytes())
        elif self.path == "/api/status":
            self._json(200, {"programs": {key: bool(value) for key, value in programs().items()}, "help": HELP})
        else:
            self._json(404, {"error": "Page not found."})

    def _payload(self, work):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= LIMIT + 1024 * 1024:
            raise ValueError("Choose files totaling no more than 80 MB.")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data;"):
            raise ValueError("Invalid local processing request.")
        self.connection.settimeout(25)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode("ascii") + self.rfile.read(size)
        )
        if not message.is_multipart():
            raise ValueError("The files could not be read. Select them again.")
        metadata = None
        paths = []
        original_names = []
        total = 0
        for part in message.iter_parts():
            name = part.get_param("name", header="Content-Disposition")
            body = part.get_payload(decode=True)
            if not isinstance(body, bytes):
                raise ValueError("The files could not be read. Select them again.")
            if name == "metadata" and metadata is None and len(body) < 150_000:
                metadata = json.loads(body)
            elif name == "file" and part.get_filename() and len(paths) < 40:
                total += len(body)
                if total > LIMIT:
                    raise ValueError("Choose files totaling no more than 80 MB.")
                original = Path(part.get_filename().replace("\\", "/")).name
                suffix = Path(original).suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,6}", suffix):
                    raise ValueError("Select a file with a supported extension.")
                destination = work / f"input-{len(paths) + 1}{suffix}"
                destination.write_bytes(body)
                paths.append(destination)
                original_names.append(original)
            else:
                raise ValueError("Choose up to 40 valid input files.")
        if not isinstance(metadata, dict) or not paths:
            raise ValueError("Choose at least one input file.")
        return metadata, paths, original_names

    def do_POST(self):
        expected = f"http://127.0.0.1:{self.server.server_port}"
        token = self.headers.get("X-LembarKlik-Token", "").encode("utf-8")
        if not (self._valid_host() and self.headers.get("Origin") == expected
                and hmac.compare_digest(token, self.server.token.encode("ascii"))):
            self._json(403, {"error": "Reopen LembarKlik from its launcher."})
            return
        if self.path not in {"/api/run", "/api/inspect"}:
            self._json(404, {"error": "Tool not found."})
            return
        if not WORKER.acquire(blocking=False):
            self._json(409, {"error": "Another local task is running. Try again when it finishes."})
            return
        try:
            with TemporaryDirectory(prefix="lembarklik-") as directory:
                work = Path(directory)
                metadata, paths, names = self._payload(work)
                if self.path == "/api/inspect":
                    self._inspect(metadata, paths[0])
                else:
                    self._run(metadata, paths, names, work)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (PyPdfError, pdfium.PdfiumError, OSError):
            self._json(422, {"error": "Could not read or write the file. Check its format and permissions."})
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            detail = str(exc) if type(exc) is ValueError else "Check the selected files and options."
            self._json(422, {"error": detail})
        except Exception:
            self._json(500, {"error": "The document could not be processed. Try another file."})
        finally:
            WORKER.release()

    def _inspect(self, metadata, source):
        if source.suffix != ".pdf":
            raise ValueError("Choose a PDF to preview.")
        reader = _reader(source)
        total = len(reader.pages)
        if metadata.get("kind") == "forms":
            fields = []
            for name, field in (reader.get_fields() or {}).items():
                if field.get("/FT") in {"/Tx", "/Ch"}:
                    value = field.get("/V")
                    fields.append([name, str(value) if value is not None else ""])
            self._json(200, {"pages": total, "fields": fields})
        else:
            page = metadata.get("page", 1)
            if type(page) is not int or not 1 <= page <= total:
                raise ValueError(f"Choose a page between 1 and {total}.")
            encoded = base64.b64encode(preview(source, page)).decode("ascii")
            self._json(200, {"pages": total, "image": f"data:image/png;base64,{encoded}"})

    def _run(self, metadata, paths, names, work):
        key = metadata.get("tool")
        if not isinstance(key, str) or key not in TOOLS:
            raise ValueError("Choose a LembarKlik tool.")
        options = metadata.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("Check the tool options.")
        definitions = {field[0]: field for field in TOOLS[key].fields}
        if options.keys() - definitions.keys():
            raise ValueError("Check the tool options.")
        for name, value in options.items():
            field_type = definitions[name][2]
            if field_type == "select" and value not in definitions[name][4]:
                raise ValueError("Choose an available setting.")
            if field_type in {"text", "password", "number"} and (
                not isinstance(value, str) or len(value) > 4000
            ):
                raise ValueError("Check the text entered in the tool options.")
        extension = TOOLS[key].output
        output = work / f"result.{extension}"
        message = execute(key, paths, output, work, options)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(names[0]).stem).strip(".-_")[:75] or "document"
        filename = f"{stem}-{key.replace('_', '-')}.{extension}"
        if not output.is_file():
            raise ValueError(message or "No output was created.")
        if output.stat().st_size > 150 * 1024 * 1024:
            raise ValueError("The result exceeds 150 MB. Try a smaller page range.")
        self._reply(200, "application/octet-stream", output.read_bytes(), filename=filename)


def create_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.token = secrets.token_urlsafe(32)
    return server


def main():
    parser = argparse.ArgumentParser(description="Start LembarKlik in your local browser.")
    parser.add_argument("--no-browser", action="store_true", help="Print the address without opening a browser.")
    arguments = parser.parse_args()
    with create_server() as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"LembarKlik is ready at {url}\nKeep this window open while using LembarKlik.", flush=True)
        if not arguments.no_browser:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
