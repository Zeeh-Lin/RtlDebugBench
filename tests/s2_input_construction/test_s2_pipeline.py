"""Tests for the S2 orchestrator and final assembly."""

import json
from pathlib import Path

import pytest

from s2_input_construction.s2_pipeline import (
    assemble_final_outputs,
    determine_org_repo,
)


def test_determine_org_repo_from_filename():
    path = Path("datasets/s2/lowRISC__ibex_s01_06_single_rtl_candidates.jsonl")
    org, repo = determine_org_repo(path, [])
    assert org == "lowRISC"
    assert repo == "ibex"


def test_determine_org_repo_from_record():
    path = Path("candidates.jsonl")
    org, repo = determine_org_repo(path, [{"repo": "openhwgroup/cva6"}])
    assert org == "openhwgroup"
    assert repo == "cva6"


def test_determine_org_repo_raises_when_unknown():
    path = Path("candidates.jsonl")
    with pytest.raises(ValueError):
        determine_org_repo(path, [])


def test_assemble_final_outputs_passed_and_failed(tmp_path):
    s1_6_file = tmp_path / "s1.jsonl"
    s1_6_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "lang": "sv",
                "rtl_files": ["rtl/foo.sv"],
                "commit_id": "base123",
                "timestamp": "2026-06-01T00:00:00Z",
                "merge_commit_sha": "merge123",
                "pr_title": "Fix foo",
                "pr_body": "Body",
                "issue_title": "Issue",
                "issue_body": "Issue body",
                "commit_message": "msg",
                "fix_patch": "diff",
            }
        )
        + "\n"
        + json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 2,
                "lang": "sv",
                "rtl_files": ["rtl/bar.sv"],
                "commit_id": "base456",
                "timestamp": "2026-06-02T00:00:00Z",
                "merge_commit_sha": "merge456",
                "pr_title": "Fix bar",
                "pr_body": "Bar body",
                "issue_title": "Bar issue",
                "issue_body": "Bar issue body",
                "commit_message": "bar msg",
                "fix_patch": "bar diff",
            }
        )
        + "\n"
    )

    materials_file = tmp_path / "m.jsonl"
    materials_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "fixed_rtl_code": "module foo; endmodule",
            }
        )
        + "\n"
    )

    selected_file = tmp_path / "sel.jsonl"
    selected_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "selected_doc_paths": ["doc/foo.md"],
                "reason": "related",
            }
        )
        + "\n"
    )

    specs_file = tmp_path / "specs.jsonl"
    specs_file.write_text(
        json.dumps(
            {"repo": "lowRISC/ibex", "pr_id": 1, "spec": "# Foo Spec"}
        )
        + "\n"
        + json.dumps(
            {"repo": "lowRISC/ibex", "pr_id": 2, "spec": "# Bar Spec"}
        )
        + "\n"
    )

    review_file = tmp_path / "review.jsonl"
    review_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "passed": True,
                "checks": {
                    "describes_correct_behavior": True,
                    "no_patch_leakage": True,
                    "sufficient_for_diagnosis": True,
                },
                "reasoning": "Looks good",
            }
        )
        + "\n"
        + json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 2,
                "passed": False,
                "checks": {
                    "describes_correct_behavior": False,
                    "no_patch_leakage": True,
                    "sufficient_for_diagnosis": False,
                },
                "reasoning": "Leaks patch",
            }
        )
        + "\n"
    )

    output_file = tmp_path / "s02_inputs.jsonl"
    manual_file = tmp_path / "manual_review_queue.jsonl"

    assemble_final_outputs(
        review_file=review_file,
        specs_file=specs_file,
        selected_docs_file=selected_file,
        materials_file=materials_file,
        s1_6_file=s1_6_file,
        output_file=output_file,
        manual_review_file=manual_file,
        skip_existing=False,
    )

    passed = [
        json.loads(line)
        for line in output_file.read_text().splitlines()
        if line.strip()
    ]
    assert len(passed) == 1
    assert passed[0]["input"]["spec"] == "# Foo Spec"
    assert passed[0]["input"]["commit_id"] == "base123"
    assert passed[0]["aux"]["selected_doc_paths"] == ["doc/foo.md"]
    assert passed[0]["bug_info"] == []

    failed = [
        json.loads(line)
        for line in manual_file.read_text().splitlines()
        if line.strip()
    ]
    assert len(failed) == 1
    assert failed[0]["pr_id"] == 2
    assert failed[0]["review_result"]["passed"] is False


def test_assemble_final_outputs_skips_existing(tmp_path):
    s1_6_file = tmp_path / "s1.jsonl"
    s1_6_file.write_text(
        json.dumps({"repo": "lowRISC/ibex", "pr_id": 1, "lang": "sv",
                    "rtl_files": ["rtl/foo.sv"], "commit_id": "base",
                    "timestamp": "", "merge_commit_sha": "", "pr_title": "",
                    "pr_body": "", "issue_title": "", "issue_body": "",
                    "commit_message": "", "fix_patch": ""}) + "\n"
    )
    materials_file = tmp_path / "m.jsonl"
    materials_file.write_text(
        json.dumps({"repo": "lowRISC/ibex", "pr_id": 1, "fixed_rtl_code": ""}) + "\n"
    )
    selected_file = tmp_path / "sel.jsonl"
    selected_file.write_text(
        json.dumps({"repo": "lowRISC/ibex", "pr_id": 1, "selected_doc_paths": []}) + "\n"
    )
    specs_file = tmp_path / "specs.jsonl"
    specs_file.write_text(
        json.dumps({"repo": "lowRISC/ibex", "pr_id": 1, "spec": ""}) + "\n"
    )
    review_file = tmp_path / "review.jsonl"
    review_file.write_text(
        json.dumps({"repo": "lowRISC/ibex", "pr_id": 1, "passed": True,
                    "checks": {}, "reasoning": ""}) + "\n"
    )

    output_file = tmp_path / "s02_inputs.jsonl"
    output_file.write_text(
        json.dumps({"input": {"repo": "lowRISC/ibex", "pr_id": 1}}) + "\n"
    )
    manual_file = tmp_path / "manual_review_queue.jsonl"

    assemble_final_outputs(
        review_file=review_file,
        specs_file=specs_file,
        selected_docs_file=selected_file,
        materials_file=materials_file,
        s1_6_file=s1_6_file,
        output_file=output_file,
        manual_review_file=manual_file,
        skip_existing=True,
    )

    lines = [line for line in output_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
