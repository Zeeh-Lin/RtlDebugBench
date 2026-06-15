# Copyright (c) 2024 Bytedance Ltd. and/or its affiliates

#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at

#      http://www.apache.org/licenses/LICENSE-2.0

#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

# s1_06_filter_patches.py - S1.6: keep only PRs that modify exactly one RTL file.
import argparse
import json
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from tqdm import tqdm

# Allow direct execution: python src/s1_collection/s1_06_filter_patches.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s1_collection.util import is_test_path, rtl_lang


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="S1.6: filter PRs to keep only those that modify exactly one RTL file."
    )
    parser.add_argument(
        "--patches_file",
        type=Path,
        required=True,
        help="Path to s01_05_patches.jsonl produced by S1.5.",
    )
    parser.add_argument(
        "--out_dir", type=Path, required=True, help="Output directory path."
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=min(32, os.cpu_count() + 4 if os.cpu_count() else 10),
        help="Number of worker threads for filtering.",
    )
    return parser


def _rtl_files(modified_files: list[str]) -> list[str]:
    """Return RTL source files, excluding testbench/test paths."""
    result = []
    for path in modified_files:
        if rtl_lang(path) and not is_test_path(path):
            result.append(path)
    return result


def _first_issue_text(resolved_issues: list[Any]) -> tuple[str, str]:
    """Extract title/body from the first resolved issue dict."""
    if not resolved_issues:
        return "", ""
    first = resolved_issues[0]
    if isinstance(first, dict):
        return first.get("title", "") or "", first.get("body", "") or ""
    return "", ""


def _commit_message(commits: list[dict]) -> str:
    """Return the message of the last commit, or empty string."""
    if commits and isinstance(commits, list):
        last = commits[-1]
        if isinstance(last, dict):
            return last.get("message", "") or ""
    return ""


def process_pr(pr_data: dict) -> dict | None:
    """Keep PR only if it modifies exactly one RTL source file."""
    modified_files = pr_data.get("modified_files", []) or []
    rtl_files = _rtl_files(modified_files)

    if len(rtl_files) != 1:
        return None

    # Also discard PRs that modify non-test, non-RTL files (e.g. markdown, docs).
    non_test_files = [p for p in modified_files if not is_test_path(p)]
    if len(non_test_files) != 1:
        return None

    rtl_path = rtl_files[0]
    lang = rtl_lang(rtl_path)
    if lang is None:
        return None

    base = pr_data.get("base", {}) or {}
    commit_id = base.get("sha", "")
    if not commit_id:
        return None

    issue_title, issue_body = _first_issue_text(pr_data.get("resolved_issues", []))

    return {
        # A. Fields that enter the final input package
        "repo": f"{pr_data.get('org', '')}/{pr_data.get('repo', '')}".strip("/"),
        "pr_id": pr_data.get("number"),
        "lang": lang,
        "rtl_files": rtl_files,
        "commit_id": commit_id,
        "timestamp": pr_data.get("merged_at", ""),
        # B. Auxiliary evidence for S2/S3
        "pr_title": pr_data.get("title", "") or "",
        "pr_body": pr_data.get("body", "") or "",
        "merge_commit_sha": pr_data.get("merge_commit_sha", "") or "",
        "issue_title": issue_title,
        "issue_body": issue_body,
        "commit_message": _commit_message(pr_data.get("commits", [])),
        "fix_patch": pr_data.get("fix_patch", "") or "",
        "test_patch": pr_data.get("test_patch", "") or "",
        "modified_files": modified_files,
        "lines_added": pr_data.get("lines_added", 0),
        "lines_removed": pr_data.get("lines_removed", 0),
    }


def main(patches_file: Path, out_dir: Path, num_workers: int):
    print("Starting S1.6: single-RTL-file filter")
    print(f"Input patches file: {patches_file}")
    print(f"Output directory: {out_dir}")
    print(f"Number of workers: {num_workers}")

    if not patches_file.exists():
        print(f"Error: Input file not found: {patches_file}")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Derive output filename from input filename.
    base_name = patches_file.name
    if "_s01_05_patches.jsonl" in base_name:
        output_name = base_name.replace("_s01_05_patches.jsonl", "_s01_06_single_rtl_candidates.jsonl")
    elif "_patches.jsonl" in base_name:
        output_name = base_name.replace("_patches.jsonl", "_single_rtl_candidates.jsonl")
    else:
        output_name = f"{base_name.split('.')[0]}_single_rtl_candidates.jsonl"
    output_file_path = out_dir / output_name

    all_prs = []
    with open(patches_file, "r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                all_prs.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"Warning: Skipping malformed JSON line in {patches_file}")
    print(f"Loaded {len(all_prs)} PRs.")

    if not all_prs:
        print("No PRs loaded. Writing empty output file.")
        with open(output_file_path, "w", encoding="utf-8") as outfile:
            pass
        return

    filtered: list[dict] = []
    with ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="S16-Filter") as executor:
        future_to_pr_num = {}
        for pr_data_item in tqdm(all_prs, desc="Submitting PRs for single-RTL filter"):
            future = executor.submit(process_pr, pr_data_item)
            future_to_pr_num[future] = pr_data_item.get("number", "unknown")

        for future in tqdm(as_completed(future_to_pr_num), total=len(all_prs), desc="Filtering PRs"):
            pr_num = future_to_pr_num[future]
            try:
                result = future.result()
                if result:
                    filtered.append(result)
            except Exception as exc:
                print(f"PR #{pr_num} generated an exception during filtering: {exc}")

    filtered.sort(key=lambda x: x.get("pr_id", 0), reverse=True)

    print(f"Finished filtering. {len(filtered)} PRs passed single-RTL filter.")
    print(f"Writing {len(filtered)} candidates to {output_file_path}...")
    with open(output_file_path, "w", encoding="utf-8") as outfile:
        for pr_data in tqdm(filtered, desc="Writing candidates"):
            outfile.write(json.dumps(pr_data, ensure_ascii=False) + "\n")

    print(f"Successfully wrote S1.6 output to {output_file_path}.")


if __name__ == "__main__":
    parser = get_parser()
    args = parser.parse_args()
    main(args.patches_file, args.out_dir, args.num_workers)
