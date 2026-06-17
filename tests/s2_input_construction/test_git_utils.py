from pathlib import Path
from unittest.mock import MagicMock
import subprocess

import pytest

from s2_input_construction.git_utils import (
    repo_dir_for,
    repo_to_url,
    read_file_at_commit,
    fetch_commit,
    list_files_at_commit,
    ensure_repo,
)


def test_repo_dir_for_sanitizes_slash():
    assert repo_dir_for(Path("/cache"), "lowRISC/ibex") == Path("/cache/lowRISC__ibex")


def test_repo_to_url():
    assert repo_to_url("lowRISC/ibex") == "https://github.com/lowRISC/ibex.git"


def test_read_file_at_commit_uses_git_show(mocker, tmp_path):
    mocker.patch(
        "s2_input_construction.git_utils._run_git",
        return_value=MagicMock(stdout="hello", stderr="", returncode=0),
    )
    result = read_file_at_commit(tmp_path, "abc123", "rtl/foo.sv")
    assert result == "hello"


def test_fetch_commit_rejects_empty_sha():
    with pytest.raises(ValueError, match="commit_sha is required"):
        fetch_commit(Path("/fake"), "")


def test_fetch_commit_raises_on_failure(mocker, tmp_path):
    mocker.patch(
        "s2_input_construction.git_utils._run_git",
        return_value=MagicMock(returncode=1, stderr="fatal"),
    )
    with pytest.raises(RuntimeError, match="Failed to fetch"):
        fetch_commit(tmp_path, "abc123")


def test_list_files_at_commit_filters_subdirs_and_extensions(mocker, tmp_path):
    mocker.patch(
        "s2_input_construction.git_utils._run_git",
        return_value=MagicMock(
            stdout="doc/a.md\ndoc/b.rst\nrtl/foo.sv\n", stderr="", returncode=0
        ),
    )
    result = list_files_at_commit(
        tmp_path, "abc123", subdirs=["doc"], extensions={".md"}
    )
    assert result == ["doc/a.md"]


def test_ensure_repo_initializes_when_git_missing(tmp_path):
    repo_dir = tmp_path / "repo"
    ensure_repo(repo_dir, "https://github.com/lowRISC/ibex.git")
    assert (repo_dir / ".git").exists()
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "lowRISC/ibex.git" in result.stdout
