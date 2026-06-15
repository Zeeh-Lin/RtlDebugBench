"""Lightweight git helpers for reading files at arbitrary commits."""

import subprocess
from pathlib import Path


def repo_dir_for(cache_root: Path, repo: str) -> Path:
    """Return local cache directory for a repo, replacing '/' with '__'."""
    return cache_root / repo.replace("/", "__")


def repo_to_url(repo: str) -> str:
    """Convert 'org/repo' to a GitHub HTTPS URL."""
    return f"https://github.com/{repo}.git"


def _run_git(repo_dir: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in repo_dir."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=check,
    )


def ensure_repo(repo_dir: Path, repo_url: str) -> None:
    """Initialize a bare-ish local repo and add the remote."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        _run_git(repo_dir, ["init", "--quiet"])
        _run_git(repo_dir, ["remote", "add", "origin", repo_url])


def fetch_commit(repo_dir: Path, commit_sha: str) -> None:
    """Shallow-fetch a single commit from origin."""
    if not commit_sha:
        raise ValueError("commit_sha is required")
    _run_git(repo_dir, ["fetch", "--depth=1", "origin", commit_sha], check=False)


def list_files_at_commit(
    repo_dir: Path,
    commit_sha: str,
    subdirs: list[str] | None = None,
    extensions: set[str] | None = None,
) -> list[str]:
    """List files under subdirs with given extensions at commit_sha."""
    result = _run_git(repo_dir, ["ls-tree", "-r", "--name-only", commit_sha])
    files = [line for line in result.stdout.splitlines() if line.strip()]
    if subdirs:
        files = [f for f in files if any(f.startswith(d + "/") for d in subdirs)]
    if extensions:
        files = [f for f in files if Path(f).suffix.lower() in extensions]
    return files


def read_file_at_commit(repo_dir: Path, commit_sha: str, file_path: str) -> str:
    """Read a file as it existed at commit_sha."""
    result = _run_git(repo_dir, ["show", f"{commit_sha}:{file_path}"])
    return result.stdout
