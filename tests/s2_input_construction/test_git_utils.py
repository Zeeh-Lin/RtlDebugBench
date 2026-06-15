from pathlib import Path
from unittest.mock import MagicMock

import pytest

from s2_input_construction import git_utils


def test_repo_dir_for_sanitizes_slash():
    assert git_utils.repo_dir_for(Path("/cache"), "lowRISC/ibex") == Path("/cache/lowRISC__ibex")


def test_repo_to_url():
    assert git_utils.repo_to_url("lowRISC/ibex") == "https://github.com/lowRISC/ibex.git"


def test_read_file_at_commit_uses_git_show(mocker, tmp_path):
    mocker.patch(
        "s2_input_construction.git_utils._run_git",
        return_value=MagicMock(stdout="hello", stderr="", returncode=0),
    )
    result = git_utils.read_file_at_commit(tmp_path, "abc123", "rtl/foo.sv")
    assert result == "hello"
