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

"""Lightweight git helpers for reading files at arbitrary commits."""

import subprocess
from pathlib import Path


class GitFetchError(RuntimeError):
    """Raised when a git fetch or checkout operation fails."""


class GitReadError(RuntimeError):
    """Raised when reading a file or tree from git fails."""


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
    """Initialize a local repo and add the remote if it does not yet exist."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        _run_git(repo_dir, ["init", "--quiet"])
        _run_git(repo_dir, ["remote", "add", "origin", repo_url])


def commit_exists(repo_dir: Path, commit_sha: str) -> bool:
    """Return True if commit_sha is already available in the local repository."""
    result = _run_git(
        repo_dir,
        ["cat-file", "-e", f"{commit_sha}^{{commit}}"],
        check=False,
    )
    return result.returncode == 0


def fetch_commit(repo_dir: Path, commit_sha: str) -> None:
    """Shallow-fetch a single commit from origin if it is not already present."""
    if not commit_sha:
        raise ValueError("commit_sha is required")
    if commit_exists(repo_dir, commit_sha):
        return
    result = _run_git(repo_dir, ["fetch", "--depth=1", "origin", commit_sha], check=False)
    if result.returncode != 0:
        raise GitFetchError(
            f"Failed to fetch {commit_sha} in {repo_dir.name}: {result.stderr.strip()}"
        )


def list_files_at_commit(
    repo_dir: Path,
    commit_sha: str,
    subdirs: list[str] | None = None,
    extensions: set[str] | None = None,
) -> list[str]:
    """List files under subdirs with given extensions at commit_sha."""
    cleaned_subdirs = [d.rstrip("/") for d in subdirs] if subdirs else []
    result = _run_git(repo_dir, ["ls-tree", "-r", "--name-only", commit_sha])
    files = result.stdout.splitlines()
    if cleaned_subdirs:
        files = [
            f
            for f in files
            if any(f.startswith(d + "/") for d in cleaned_subdirs)
        ]
    if extensions:
        files = [f for f in files if Path(f).suffix.lower() in extensions]
    return files


def read_file_at_commit(repo_dir: Path, commit_sha: str, file_path: str) -> str:
    """Read a file as it existed at commit_sha."""
    result = _run_git(
        repo_dir, ["show", f"{commit_sha}:{file_path}"], check=False
    )
    if result.returncode != 0:
        raise GitReadError(
            f"Failed to read {file_path} at {commit_sha}: {result.stderr.strip()}"
        )
    return result.stdout
