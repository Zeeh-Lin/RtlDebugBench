"""Tests for the S2.1 material-fetch step."""

import json
from pathlib import Path

import pytest

from s2_input_construction.s2_01_fetch_materials import (
    fetch_materials_for_pr,
    load_repo_config,
    process_materials,
)


def test_load_repo_config(tmp_path):
    config = tmp_path / "repo_config.yaml"
    config.write_text(
        "lowRISC/ibex:\n"
        "  url: https://github.com/lowRISC/ibex.git\n"
        "  doc_dirs:\n    - doc\n"
        "  rtl_dir: rtl\n"
    )
    cfg = load_repo_config(config)
    assert cfg["lowRISC/ibex"]["url"] == "https://github.com/lowRISC/ibex.git"


def test_fetch_materials_for_pr(mocker, tmp_path):
    mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.ensure_repo"
    )
    mocker.patch("s2_input_construction.s2_01_fetch_materials.fetch_commit")
    mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.read_file_at_commit",
        side_effect=lambda _d, _c, p: f"content:{p}",
    )
    mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.list_files_at_commit",
        return_value=["doc/pmp.rst", "doc/csr.md"],
    )

    pr = {
        "repo": "lowRISC/ibex",
        "pr_id": 2449,
        "merge_commit_sha": "abc123",
        "rtl_files": ["rtl/ibex_pmp.sv"],
        "lang": "sv",
    }
    repo_config = {
        "lowRISC/ibex": {
            "url": "https://github.com/lowRISC/ibex.git",
            "doc_dirs": ["doc"],
        }
    }

    result = fetch_materials_for_pr(pr, tmp_path, repo_config)
    assert result["repo"] == "lowRISC/ibex"
    assert result["pr_id"] == 2449
    assert result["lang"] == "sv"
    assert result["rtl_files"] == ["rtl/ibex_pmp.sv"]
    assert result["rtl_file"] == "rtl/ibex_pmp.sv"
    assert result["fixed_rtl_code"] == "content:rtl/ibex_pmp.sv"
    assert len(result["doc_toc"]) == 2
    assert result["doc_full_texts"]["doc/pmp.rst"].startswith("content:")


def test_process_materials(mocker, tmp_path):
    mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.ensure_repo"
    )
    mocker.patch("s2_input_construction.s2_01_fetch_materials.fetch_commit")
    mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.read_file_at_commit",
        side_effect=lambda _d, _c, p: f"content:{p}",
    )
    mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.list_files_at_commit",
        return_value=["doc/pmp.rst"],
    )

    input_file = tmp_path / "lowRISC__ibex_s01_06_single_rtl_candidates.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "merge_commit_sha": "abc",
                "rtl_files": ["rtl/foo.sv"],
                "lang": "sv",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    out_dir = tmp_path / "out"

    repo_config = {
        "lowRISC/ibex": {
            "url": "https://github.com/lowRISC/ibex.git",
            "doc_dirs": ["doc"],
        }
    }

    process_materials(
        input_file=input_file,
        output_file=out_dir / "lowRISC__ibex_s2_01_materials.jsonl",
        failed_checkout_file=out_dir / "lowRISC__ibex_failed_checkout.jsonl",
        failed_read_file=out_dir / "lowRISC__ibex_failed_read.jsonl",
        cache_root=tmp_path / "repos",
        repo_config=repo_config,
        skip_existing=False,
        num_workers=1,
    )

    materials = list(
        json.loads(line)
        for line in (out_dir / "lowRISC__ibex_s2_01_materials.jsonl").read_text().splitlines()
        if line.strip()
    )
    assert len(materials) == 1
    assert materials[0]["fixed_rtl_code"] == "content:rtl/foo.sv"
    assert (out_dir / "lowRISC__ibex_failed_checkout.jsonl").exists()
    assert (out_dir / "lowRISC__ibex_failed_read.jsonl").exists()


def test_process_materials_skips_existing(mocker, tmp_path):
    input_file = tmp_path / "lowRISC__ibex_s01_06_single_rtl_candidates.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "repo": "lowRISC/ibex",
                "pr_id": 1,
                "merge_commit_sha": "abc",
                "rtl_files": ["rtl/foo.sv"],
                "lang": "sv",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "lowRISC__ibex_s2_01_materials.jsonl"
    existing.write_text(
        json.dumps({"repo": "lowRISC/ibex", "pr_id": 1}) + "\n"
    )

    fetch_mock = mocker.patch(
        "s2_input_construction.s2_01_fetch_materials.fetch_materials_for_pr"
    )

    process_materials(
        input_file=input_file,
        output_file=existing,
        failed_checkout_file=out_dir / "fc.jsonl",
        failed_read_file=out_dir / "fr.jsonl",
        cache_root=tmp_path / "repos",
        repo_config={},
        skip_existing=True,
        num_workers=1,
    )

    fetch_mock.assert_not_called()
