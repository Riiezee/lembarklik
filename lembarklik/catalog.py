"""Shared tool definitions for the local web interface and file validation."""

from dataclasses import dataclass
import json
from pathlib import Path

WEB = Path(__file__).with_name("web")
MULTI = {"merge", "compare", "images_to_pdf", "scan", "sign", "edit"}


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    category: str
    description: str
    extensions: str
    output: str
    minimum: int
    fields: list
    dependency: str = ""


TOOLS = {
    row[0]: Tool(*row)
    for row in json.loads((WEB / "tools.json").read_text(encoding="utf-8"))
}
