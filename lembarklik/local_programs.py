"""Locate and call optional programs installed on this device."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from .engine import _reader


def _find(names, folders):
    for name in names:
        if found := shutil.which(name):
            return Path(found)
        for folder in folders:
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None


def configured_model():
    path = os.environ.get("LEMBARKLIK_GGUF") or os.environ.get("LOCALPDF_GGUF")
    return Path(path).resolve() if path else None


def programs():
    windows_folders = [Path(os.environ[key]) for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
                       if os.environ.get(key)]
    model_path = configured_model()
    return {
        "office": _find(("soffice.com", "soffice"), [p / "LibreOffice" / "program" for p in windows_folders]),
        "ocr": _find(("tesseract", "tesseract.exe"), [p / "Tesseract-OCR" for p in windows_folders]),
        "model": _find(("llama-cli", "llama-cli.exe"), []) if model_path and model_path.is_file() else None,
    }


HELP = {
    "office": "Run setup.bat to install LibreOffice, then reopen LembarKlik.",
    "ocr": "Install Tesseract and the chosen language data to use this tool.",
    "model": "Install llama-cli and set LEMBARKLIK_GGUF to a local model file. See the README.",
}


def require(key):
    executable = programs()[key]
    if executable is None:
        raise ValueError(HELP[key])
    return executable


def run_local(arguments, *, timeout=150, stdin=None):
    try:
        result = subprocess.run(
            [str(arg) for arg in arguments], input=stdin, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("The local converter timed out. Try a smaller document.") from exc
    if result.returncode:
        raise ValueError("The local converter could not finish. Check the file and required language data.")
    return result.stdout.strip()


def office_pdf(source, output, work, archive=False):
    office = require("office")
    profile = work / "office-profile"
    user_profile = profile / "user"
    user_profile.mkdir(parents=True)
    (user_profile / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '</oor:items>',
        encoding="utf-8",
    )
    export_filter = "pdf"
    if archive:
        _reader(source)
        options = json.dumps({"SelectPdfVersion": {"type": "long", "value": "2"}}, separators=(",", ":"))
        export_filter = f"pdf:draw_pdf_Export:{options}"
    folder = work / "office-output"
    folder.mkdir()
    run_local([office, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--nologo",
               "--norestore", "--convert-to", export_filter, "--outdir", folder, source])
    generated = folder / f"{source.stem}.pdf"
    if not generated.is_file():
        raise ValueError("LibreOffice did not produce a PDF. Check that the source opens in LibreOffice.")
    _reader(generated)
    generated.replace(output)
