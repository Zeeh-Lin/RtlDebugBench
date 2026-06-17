"""Tests for document title extraction and formatting helpers."""

from s2_input_construction.parsers import (
    extract_title,
    format_doc_toc,
    format_official_spec,
)


def test_extract_md_title():
    content = "# Physical Memory Protection\n\nSome body text."
    assert extract_title("doc/pmp.md", content) == "Physical Memory Protection"


def test_extract_md_title_ignores_later_headings():
    content = "# First\n\n# Second\n"
    assert extract_title("doc/t.md", content) == "First"


def test_extract_rst_title_directive():
    content = ".. title:: Physical Memory Protection\n\nbody"
    assert extract_title("doc/pmp.rst", content) == "Physical Memory Protection"


def test_extract_rst_title_underline():
    content = "Physical Memory Protection\n==========================\n\nbody"
    assert extract_title("doc/pmp.rst", content) == "Physical Memory Protection"


def test_extract_title_fallback_normalizes_filename():
    assert extract_title("doc/pmp_unit.sv.md", "no heading") == "Pmp Unit Sv"


def test_format_doc_toc():
    toc = [
        {"path": "doc/a.md", "title": "Document A"},
        {"path": "doc/b.rst", "title": "Document B"},
    ]
    assert format_doc_toc(toc) == "- doc/a.md: Document A\n- doc/b.rst: Document B"


def test_format_doc_toc_empty():
    assert format_doc_toc([]) == "No official documents available."


def test_format_official_spec_empty():
    assert format_official_spec([], {}) == "None"


def test_format_official_spec_selected_docs():
    selected = ["doc/pmp.rst"]
    full_texts = {"doc/pmp.rst": "PMP rules."}
    assert format_official_spec(selected, full_texts) == "### doc/pmp.rst\n\nPMP rules."
