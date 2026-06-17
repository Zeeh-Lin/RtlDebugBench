# Copyright (c) 2024 Bytedance Ltd. and/or its affiliates
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Document title extraction and formatting helpers for S2 prompts."""

import re
from pathlib import Path


def _extract_md_title(content: str) -> str | None:
    """Return the first Markdown ATX heading title, or None."""
    for line in content.splitlines():
        stripped = line.strip()
        match = re.match(r"^#\s+(.+)$", stripped)
        if match:
            return match.group(1).strip()
    return None


def _extract_rst_title(content: str) -> str | None:
    """Return the first reStructuredText title, or None.

    Tries ``.. title::`` first, then falls back to an underlined heading
    (a text line followed by a line made of a single repeated punctuation
    character such as ``=``, ``-``, ``~``, or ``^``).
    """
    # Explicit .. title:: directive
    for line in content.splitlines():
        stripped = line.strip()
        match = re.match(r"^\.\.\s*title::\s*(.+)$", stripped, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Underlined heading
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if (
                next_line
                and len(set(next_line)) == 1
                and next_line[0] in "-=~^"
                and len(next_line) >= len(stripped)
            ):
                return stripped
    return None


def extract_title(file_path: str, content: str) -> str:
    """Extract a human-readable title from a Markdown or RST document.

    Falls back to a normalized filename if no title is found.
    """
    suffix = Path(file_path).suffix.lower()
    title: str | None = None
    if suffix == ".md":
        title = _extract_md_title(content)
    elif suffix == ".rst":
        title = _extract_rst_title(content)

    if title:
        return title

    # Fallback: normalize the filename stem to a readable title.
    stem = Path(file_path).stem
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", stem).strip()
    return normalized.title() or stem


def format_doc_toc(doc_toc: list[dict[str, str]]) -> str:
    """Render a document table of contents for the doc-selection prompt."""
    if not doc_toc:
        return "No official documents available."
    lines = []
    for entry in doc_toc:
        path = entry.get("path", "")
        title = entry.get("title", "")
        lines.append(f"- {path}: {title}")
    return "\n".join(lines)


def format_official_spec(selected_doc_paths: list[str], doc_full_texts: dict[str, str]) -> str:
    """Render selected documents with their full text for the spec-generation prompt."""
    if not selected_doc_paths:
        return "None"
    parts = []
    for path in selected_doc_paths:
        text = doc_full_texts.get(path, "")
        parts.append(f"### {path}\n\n{text}")
    return "\n\n".join(parts)
