"""Tests for the S2.2 document-selection step."""

import json
from pathlib import Path

from s2_input_construction.s2_02_select_docs import (
    build_prompt,
    process_select_docs,
)


def test_build_prompt_contains_doc_toc():
    material = {
        "repo": "lowRISC/ibex",
        "rtl_file": "rtl/ibex_pmp.sv",
        "fixed_rtl_code": "module ibex_pmp(); endmodule",
        "doc_toc": [
            {"path": "doc/pmp.rst", "title": "Physical Memory Protection"},
        ],
    }
    prompt = build_prompt(material)
    assert "rtl/ibex_pmp.sv" in prompt
    assert "Physical Memory Protection" in prompt
    assert "selected_doc_paths" in prompt


def test_process_select_docs(mocker, tmp_path):
    materials_file = tmp_path / "lowRISC__ibex_s2_01_materials.jsonl"
    materials_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "rtl_file": "rtl/foo.sv",
                "fixed_rtl_code": "module foo; endmodule",
                "doc_toc": [{"path": "doc/pmp.rst", "title": "PMP"}],
                "doc_full_texts": {"doc/pmp.rst": "PMP rules."},
            }
        )
        + "\n"
    )
    out_dir = tmp_path / "out"

    mock_client = mocker.MagicMock()
    mock_client.call_json.return_value = {
        "selected_doc_paths": ["doc/pmp.rst"],
        "reason": "PMP related",
    }

    process_select_docs(
        materials_file=materials_file,
        output_file=out_dir / "lowRISC__ibex_s2_02_selected_docs.jsonl",
        failed_llm_file=out_dir / "lowRISC__ibex_failed_llm.jsonl",
        llm_client=mock_client,
        skip_existing=False,
        num_workers=1,
    )

    selected = list(
        json.loads(line)
        for line in (out_dir / "lowRISC__ibex_s2_02_selected_docs.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    )
    assert len(selected) == 1
    assert selected[0]["selected_doc_paths"] == ["doc/pmp.rst"]
    assert selected[0]["reason"] == "PMP related"
    mock_client.call_json.assert_called_once()


def test_process_select_docs_filters_invalid_paths(mocker, tmp_path):
    materials_file = tmp_path / "lowRISC__ibex_s2_01_materials.jsonl"
    materials_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "rtl_file": "rtl/foo.sv",
                "fixed_rtl_code": "module foo; endmodule",
                "doc_toc": [],
                "doc_full_texts": {"doc/existing.rst": "content"},
            }
        )
        + "\n"
    )
    out_dir = tmp_path / "out"

    mock_client = mocker.MagicMock()
    mock_client.call_json.return_value = {
        "selected_doc_paths": [
            "doc/missing.rst",
            "doc/existing.rst",
            "doc/existing.rst",
        ],
        "reason": "related",
    }

    process_select_docs(
        materials_file=materials_file,
        output_file=out_dir / "lowRISC__ibex_s2_02_selected_docs.jsonl",
        failed_llm_file=out_dir / "lowRISC__ibex_failed_llm.jsonl",
        llm_client=mock_client,
        skip_existing=False,
        num_workers=1,
    )

    selected = list(
        json.loads(line)
        for line in (out_dir / "lowRISC__ibex_s2_02_selected_docs.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    )
    assert selected[0]["selected_doc_paths"] == ["doc/existing.rst"]


def test_process_select_docs_records_llm_failure(mocker, tmp_path):
    materials_file = tmp_path / "lowRISC__ibex_s2_01_materials.jsonl"
    materials_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "rtl_file": "rtl/foo.sv",
                "fixed_rtl_code": "module foo; endmodule",
                "doc_toc": [],
                "doc_full_texts": {},
            }
        )
        + "\n"
    )
    out_dir = tmp_path / "out"

    mock_client = mocker.MagicMock()
    mock_client.call_json.side_effect = RuntimeError("timeout")

    process_select_docs(
        materials_file=materials_file,
        output_file=out_dir / "lowRISC__ibex_s2_02_selected_docs.jsonl",
        failed_llm_file=out_dir / "lowRISC__ibex_failed_llm.jsonl",
        llm_client=mock_client,
        skip_existing=False,
        num_workers=1,
    )

    failures = list(
        json.loads(line)
        for line in (out_dir / "lowRISC__ibex_failed_llm.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    )
    assert len(failures) == 1
    assert "timeout" in failures[0]["error"]
