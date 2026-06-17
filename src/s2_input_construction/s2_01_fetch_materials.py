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

from __future__ import annotations

"""S2.1: Fetch fixed RTL source and official documents for each S1.6 candidate."""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, TextIO

import yaml
from tqdm import tqdm

# Allow direct execution: python src/s2_input_construction/s2_01_fetch_materials.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s2_input_construction.git_utils import (
    GitFetchError,
    GitReadError,
    ensure_repo,
    fetch_commit,
    list_files_at_commit,
    read_file_at_commit,
    repo_dir_for,
)
from s2_input_construction.parsers import extract_title


def load_repo_config(config_path: Path) -> dict[str, Any]:
    """Load per-repository configuration from YAML."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg or {}


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping malformed lines."""
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def write_jsonl_line(file_obj: TextIO, record: dict) -> None:
    """Write a single JSON record to an already-open file object."""
    file_obj.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    file_obj.flush()


def _record_key(record: dict) -> tuple[str, int]:
    return (record["repo"], record["pr_id"])


_repo_locks: dict[str, threading.Lock] = {}


def _repo_lock(repo: str) -> threading.Lock:
    """Return a per-repo lock used to serialize git operations in the cache."""
    lock = _repo_locks.get(repo)
    if lock is None:
        lock = threading.Lock()
        _repo_locks[repo] = lock
    return lock


def fetch_materials_for_pr(
    pr: dict,
    cache_root: Path,
    repo_config: dict,
    git_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Shallow-fetch a PR's merge commit and read fixed RTL + docs."""
    repo = pr["repo"]
    if repo not in repo_config:
        raise ValueError(f"Repo {repo} not found in repo_config")

    cfg = repo_config[repo]
    merge_commit_sha = pr["merge_commit_sha"]
    rtl_files = pr.get("rtl_files") or []
    if not rtl_files:
        raise ValueError("rtl_files is empty")
    rtl_file = rtl_files[0]

    repo_dir = repo_dir_for(cache_root, repo)
    lock = git_lock or _repo_lock(repo)
    with lock:
        ensure_repo(repo_dir, cfg["url"])
        fetch_commit(repo_dir, merge_commit_sha)

    try:
        fixed_rtl_code = read_file_at_commit(repo_dir, merge_commit_sha, rtl_file)
    except GitReadError as exc:
        raise GitReadError(f"RTL file missing at merge commit: {exc}") from exc

    doc_files = list_files_at_commit(
        repo_dir,
        merge_commit_sha,
        subdirs=cfg.get("doc_dirs"),
        extensions={".md", ".rst"},
    )

    doc_toc: list[dict[str, str]] = []
    doc_full_texts: dict[str, str] = {}
    for doc_path in doc_files:
        try:
            doc_content = read_file_at_commit(repo_dir, merge_commit_sha, doc_path)
        except GitReadError:
            # Document path exists in ls-tree but cannot be read; skip it.
            continue
        title = extract_title(doc_path, doc_content)
        doc_toc.append({"path": doc_path, "title": title})
        doc_full_texts[doc_path] = doc_content

    return {
        "repo": repo,
        "pr_id": pr["pr_id"],
        "lang": pr.get("lang", "sv"),
        "rtl_files": rtl_files,
        "rtl_file": rtl_file,
        "fixed_rtl_code": fixed_rtl_code,
        "doc_toc": doc_toc,
        "doc_full_texts": doc_full_texts,
    }


def process_materials(
    input_file: Path,
    output_file: Path,
    failed_checkout_file: Path,
    failed_read_file: Path,
    cache_root: Path,
    repo_config: dict,
    skip_existing: bool,
    num_workers: int,
) -> None:
    """Process all PRs from the S1.6 file and write materials + failure logs."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    failed_checkout_file.parent.mkdir(parents=True, exist_ok=True)
    failed_read_file.parent.mkdir(parents=True, exist_ok=True)

    existing: set[tuple[str, int]] = set()
    if skip_existing and output_file.exists():
        for rec in read_jsonl(output_file):
            existing.add(_record_key(rec))

    prs = [rec for rec in read_jsonl(input_file) if _record_key(rec) not in existing]

    mode = "a" if skip_existing else "w"
    write_lock = threading.Lock()

    def _worker(pr: dict) -> tuple[str, dict]:
        try:
            result = fetch_materials_for_pr(pr, cache_root, repo_config)
            return ("ok", result)
        except (GitFetchError, ValueError) as exc:
            # Git fetch/checkout failures (including invalid merge_commit_sha)
            # or configuration / data errors.
            return (
                "checkout",
                {"repo": pr["repo"], "pr_id": pr["pr_id"], "error": str(exc)},
            )
        except GitReadError as exc:
            return (
                "read",
                {
                    "repo": pr["repo"],
                    "pr_id": pr["pr_id"],
                    "rtl_file": (pr.get("rtl_files") or [None])[0],
                    "error": str(exc),
                },
            )
        except Exception as exc:
            # Other unexpected failures.
            return (
                "read",
                {
                    "repo": pr["repo"],
                    "pr_id": pr["pr_id"],
                    "rtl_file": (pr.get("rtl_files") or [None])[0],
                    "error": str(exc),
                },
            )

    with (
        open(output_file, mode, encoding="utf-8") as out_f,
        open(failed_checkout_file, mode, encoding="utf-8") as co_f,
        open(failed_read_file, mode, encoding="utf-8") as rd_f,
    ):
        if num_workers == 1:
            for pr in tqdm(prs, desc="S2.1 fetch materials"):
                status, payload = _worker(pr)
                if status == "ok":
                    write_jsonl_line(out_f, payload)
                elif status == "checkout":
                    write_jsonl_line(co_f, payload)
                else:
                    write_jsonl_line(rd_f, payload)
        else:
            with ThreadPoolExecutor(
                max_workers=num_workers, thread_name_prefix="S21-"
            ) as executor:
                future_to_pr = {executor.submit(_worker, pr): pr for pr in prs}
                for future in tqdm(
                    as_completed(future_to_pr),
                    total=len(prs),
                    desc="S2.1 fetch materials",
                ):
                    status, payload = future.result()
                    with write_lock:
                        if status == "ok":
                            write_jsonl_line(out_f, payload)
                        elif status == "checkout":
                            write_jsonl_line(co_f, payload)
                        else:
                            write_jsonl_line(rd_f, payload)


def _derive_output_name(input_name: str, suffix: str) -> str:
    """Derive an output filename from a known input filename."""
    markers = [
        "_s01_06_single_rtl_candidates.jsonl",
        "_single_rtl_candidates.jsonl",
        "_s2_01_materials.jsonl",
        "_s2_02_selected_docs.jsonl",
        "_s2_03_generated_specs.jsonl",
        "_s2_04_review_results.jsonl",
    ]
    for marker in markers:
        if marker in input_name:
            return input_name.replace(marker, suffix)
    stem = input_name.split(".")[0]
    return f"{stem}{suffix}"


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="S2.1: fetch fixed RTL and official documents for S1.6 candidates."
    )
    parser.add_argument(
        "--input_file", type=Path, required=True, help="Path to S1.6 candidates JSONL."
    )
    parser.add_argument(
        "--out_dir", type=Path, required=True, help="Directory for S2 outputs."
    )
    parser.add_argument(
        "--cache_root",
        type=Path,
        default=Path("datasets/repos"),
        help="Root directory for cached git repositories.",
    )
    parser.add_argument(
        "--repo_config",
        type=Path,
        default=Path(__file__).resolve().parent / "repo_config.yaml",
        help="Path to repo_config.yaml.",
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=1,
        help="Number of parallel workers (default: 1).",
    )
    parser.add_argument(
        "--skip_existing",
        action="store_true",
        help="Skip PRs already present in the materials output.",
    )
    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    repo_config = load_repo_config(args.repo_config)
    output_name = _derive_output_name(args.input_file.name, "_s2_01_materials.jsonl")
    output_file = args.out_dir / output_name
    failed_checkout_file = args.out_dir / _derive_output_name(
        args.input_file.name, "_failed_checkout.jsonl"
    )
    failed_read_file = args.out_dir / _derive_output_name(
        args.input_file.name, "_failed_read.jsonl"
    )

    process_materials(
        input_file=args.input_file,
        output_file=output_file,
        failed_checkout_file=failed_checkout_file,
        failed_read_file=failed_read_file,
        cache_root=args.cache_root,
        repo_config=repo_config,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
