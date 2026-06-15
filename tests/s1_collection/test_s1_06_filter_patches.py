import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from s1_collection.s1_06_filter_patches import process_pr


def test_process_pr_carries_merge_commit_sha():
    pr_data = {
        "org": "lowRISC",
        "repo": "ibex",
        "number": 123,
        "title": "fix pmp",
        "body": "",
        "merged_at": "2026-06-01T00:00:00Z",
        "base": {"sha": "abc123"},
        "merge_commit_sha": "def456",
        "modified_files": ["rtl/ibex_pmp.sv"],
        "commits": [{"message": "fix"}],
        "resolved_issues": [],
        "fix_patch": "patch",
        "test_patch": "",
        "lines_added": 1,
        "lines_removed": 1,
    }
    result = process_pr(pr_data)
    assert result is not None
    assert result["merge_commit_sha"] == "def456"
    assert result["commit_id"] == "abc123"
