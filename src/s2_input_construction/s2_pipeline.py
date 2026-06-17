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

"""S2 orchestrator: run fetch -> select docs -> generate spec -> review -> assemble inputs."""

import argparse
import re
import sys
from pathlib import Path

# Allow direct execution: python src/s2_input_construction/s2_pipeline.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from s2_input_construction.llm_client import LLMClient
from s2_input_construction.s2_01_fetch_materials import (
    _derive_output_name,
    _record_key,
    load_repo_config,
    process_materials,
    read_jsonl,
    write_jsonl_line,
)
from s2_input_construction.s2_02_select_docs import process_select_docs
from s2_input_construction.s2_03_generate_spec import process_generate_specs
from s2_input_construction.s2_04_review_spec import process_review_specs


def determine_org_repo(input_file: Path, s1_records: list[dict]) -> tuple[str, str]:
    """Derive org/repo from the input filename or the first record."""
    m = re.match(
        r"^([^_]+)__([^_]+)_s01_06_single_rtl_candidates\.jsonl$", input_file.name
    )
    if m:
        return m.group(1), m.group(2)
    if s1_records:
        repo_full = s1_records[0].get("repo", "")
        if "/" in repo_full:
            org, repo = repo_full.split("/", 1)
            return org, repo
    raise ValueError(
        f"Cannot determine org/repo from input filename or records: {input_file}"
    )


def _input_key(record: dict) -> tuple[str, int]:
    """Extract (repo, pr_id) from a final output record."""
    return (record["input"]["repo"], record["input"]["pr_id"])


def assemble_final_outputs(
    review_file: Path,
    specs_file: Path,
    selected_docs_file: Path,
    materials_file: Path,
    s1_6_file: Path,
    output_file: Path,
    manual_review_file: Path,
    skip_existing: bool,
) -> None:
    """Assemble s02_inputs.jsonl and manual_review_queue.jsonl from review results."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    manual_review_file.parent.mkdir(parents=True, exist_ok=True)

    existing_pass: set[tuple[str, int]] = set()
    existing_fail: set[tuple[str, int]] = set()
    if skip_existing:
        for rec in read_jsonl(output_file):
            existing_pass.add(_input_key(rec))
        for rec in read_jsonl(manual_review_file):
            existing_fail.add(_record_key(rec))

    s1_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(s1_6_file):
        s1_by_key[_record_key(rec)] = rec

    materials_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(materials_file):
        materials_by_key[_record_key(rec)] = rec

    selected_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(selected_docs_file):
        selected_by_key[_record_key(rec)] = rec

    specs_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(specs_file):
        specs_by_key[_record_key(rec)] = rec

    reviews = read_jsonl(review_file)

    mode = "a" if skip_existing else "w"
    with (
        open(output_file, mode, encoding="utf-8") as pass_f,
        open(manual_review_file, mode, encoding="utf-8") as fail_f,
    ):
        for review in reviews:
            key = _record_key(review)
            if key in existing_pass or key in existing_fail:
                continue

            s1_record = s1_by_key.get(key)
            material = materials_by_key.get(key)
            selected = selected_by_key.get(key)
            spec = specs_by_key.get(key)
            if s1_record is None or spec is None:
                print(
                    f"Warning: skipping {key}: missing "
                    f"{'S1.6 record' if s1_record is None else 'generated spec'}"
                )
                continue

            repo, pr_id = key
            if review.get("passed"):
                final_record = {
                    "input": {
                        "repo": s1_record["repo"],
                        "pr_id": s1_record["pr_id"],
                        "lang": s1_record["lang"],
                        "rtl_files": s1_record["rtl_files"],
                        "spec": spec["spec"],
                        "commit_id": s1_record["commit_id"],
                        "timestamp": s1_record["timestamp"],
                    },
                    "bug_info": [],
                    "aux": {
                        "merge_commit_sha": s1_record["merge_commit_sha"],
                        "pr_title": s1_record["pr_title"],
                        "pr_body": s1_record["pr_body"],
                        "issue_title": s1_record["issue_title"],
                        "issue_body": s1_record["issue_body"],
                        "commit_message": s1_record["commit_message"],
                        "fix_patch": s1_record["fix_patch"],
                        "fixed_rtl_code": material.get("fixed_rtl_code", "")
                        if material
                        else "",
                        "selected_doc_paths": selected.get("selected_doc_paths", [])
                        if selected
                        else [],
                    },
                }
                write_jsonl_line(pass_f, final_record)
            else:
                manual_record = {
                    "repo": repo,
                    "pr_id": pr_id,
                    "review_result": {
                        "passed": review.get("passed", False),
                        "checks": review.get("checks", {}),
                        "reasoning": review.get("reasoning", ""),
                    },
                    "generated_spec": spec["spec"],
                    "fixed_rtl_code": material.get("fixed_rtl_code", "")
                    if material
                    else "",
                    "pr_title": s1_record["pr_title"],
                    "pr_body": s1_record["pr_body"],
                    "fix_patch": s1_record["fix_patch"],
                }
                write_jsonl_line(fail_f, manual_record)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full S2 input-construction pipeline."
    )
    parser.add_argument(
        "--input_file",
        type=Path,
        required=True,
        help="Path to S1.6 single-RTL candidates JSONL.",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=Path("datasets/s2"),
        help="Root directory for S2 outputs (default: datasets/s2).",
    )
    parser.add_argument(
        "--cache_root",
        type=Path,
        default=Path("datasets/repos"),
        help="Root directory for cached git repositories (default: datasets/repos).",
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
        help="Resume by skipping PRs already present in each step's outputs.",
    )
    parser.add_argument(
        "--llm_base_url",
        type=str,
        default=None,
        help="OpenAI-compatible API base URL (default: https://api.deepseek.com).",
    )
    parser.add_argument(
        "--llm_model",
        type=str,
        default=None,
        help="Model name for LLM calls (default: deepseek-reasoner).",
    )
    parser.add_argument(
        "--llm_api_key_env",
        type=str,
        default=None,
        help="Environment variable name for the LLM API key (default: DEEPSEEK_API_KEY).",
    )
    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    s1_records = read_jsonl(args.input_file)
    if not s1_records:
        print("No S1.6 records found; nothing to do.")
        return

    org, repo = determine_org_repo(args.input_file, s1_records)
    repo_pair = f"{org}__{repo}"
    out_dir = args.out_dir / repo_pair
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"S2 output directory: {out_dir}")

    materials_file = out_dir / f"{repo_pair}_s2_01_materials.jsonl"
    selected_docs_file = out_dir / f"{repo_pair}_s2_02_selected_docs.jsonl"
    specs_file = out_dir / f"{repo_pair}_s2_03_generated_specs.jsonl"
    review_file = out_dir / f"{repo_pair}_s2_04_review_results.jsonl"
    final_output = out_dir / f"{repo_pair}_s02_inputs.jsonl"
    manual_review_file = out_dir / f"{repo_pair}_manual_review_queue.jsonl"
    failed_checkout_file = out_dir / f"{repo_pair}_failed_checkout.jsonl"
    failed_read_file = out_dir / f"{repo_pair}_failed_read.jsonl"
    failed_llm_file = out_dir / f"{repo_pair}_failed_llm.jsonl"

    repo_config = load_repo_config(args.repo_config)
    llm_client = LLMClient(
        base_url=args.llm_base_url,
        model=args.llm_model,
        api_key_env=args.llm_api_key_env,
    )
    # Force client creation here so the shared instance is safe across threads.
    _ = llm_client.client

    print("S2 pipeline start")
    print("Step 1/4: fetch materials")
    process_materials(
        input_file=args.input_file,
        output_file=materials_file,
        failed_checkout_file=failed_checkout_file,
        failed_read_file=failed_read_file,
        cache_root=args.cache_root,
        repo_config=repo_config,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )

    print("Step 2/4: select docs")
    process_select_docs(
        materials_file=materials_file,
        output_file=selected_docs_file,
        failed_llm_file=failed_llm_file,
        llm_client=llm_client,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )

    print("Step 3/4: generate specs")
    process_generate_specs(
        materials_file=materials_file,
        selected_docs_file=selected_docs_file,
        output_file=specs_file,
        failed_llm_file=failed_llm_file,
        llm_client=llm_client,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )

    print("Step 4/4: review specs")
    process_review_specs(
        specs_file=specs_file,
        s1_6_file=args.input_file,
        materials_file=materials_file,
        output_file=review_file,
        failed_llm_file=failed_llm_file,
        llm_client=llm_client,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )

    print("Assembling final outputs")
    assemble_final_outputs(
        review_file=review_file,
        specs_file=specs_file,
        selected_docs_file=selected_docs_file,
        materials_file=materials_file,
        s1_6_file=args.input_file,
        output_file=final_output,
        manual_review_file=manual_review_file,
        skip_existing=args.skip_existing,
    )
    print("S2 pipeline complete")


if __name__ == "__main__":
    main()
