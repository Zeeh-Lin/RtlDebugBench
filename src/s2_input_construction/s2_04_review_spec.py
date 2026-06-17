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

"""S2.4: Review generated specs against PR evidence and fix patches."""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

# Allow direct execution: python src/s2_input_construction/s2_04_review_spec.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from s2_input_construction.llm_client import LLMClient
from s2_input_construction.s2_01_fetch_materials import (
    _derive_output_name,
    _record_key,
    read_jsonl,
    write_jsonl_line,
)


REVIEW_PROMPT = """You are a senior RTL verification engineer. Review the following Spec for quality.

## Input Materials

### PR Contexts
{pr_contexts}

### Fix Patch
```diff
{fix_patch}
```

### RTL Source
```systemverilog
{rtl_code}
```

### Generated Specification
```markdown
{generated_spec}
```

## Checks (true / false)
1. describes_correct_behavior: Does the **generated specification** use must/shall/should to describe correct behavior? No "bug/fixed/broken/incorrectly".
2. no_patch_leakage: Does the **generated specification** contain NO signal names, variable names, assign expressions, or line numbers from the patch?
3. sufficient_for_diagnosis: Does the generated specification contain at least one behavioral assertion directly related to the functional area modified by the patch?

## Output
Output valid JSON only, no markdown fences:
{{"passed": true, "checks": {{"describes_correct_behavior": true, "no_patch_leakage": true, "sufficient_for_diagnosis": true}}, "reasoning": "one sentence summary"}}
"""


def _build_pr_contexts(pr_record: dict) -> str:
    """Assemble PR evidence sections for the review prompt."""
    parts = []
    for label, key in [
        ("PR Title", "pr_title"),
        ("PR Body", "pr_body"),
        ("Issue Title", "issue_title"),
        ("Issue Body", "issue_body"),
        ("Commit Message", "commit_message"),
    ]:
        value = (pr_record.get(key) or "").strip()
        if value:
            parts.append(f"#### {label}\n{value}")
    return "\n\n".join(parts) or "No PR context available."


def build_prompt(
    pr_record: dict, material: dict | None, spec_record: dict
) -> str:
    """Build the spec-review prompt for a single PR."""
    rtl_code = material.get("fixed_rtl_code", "") if material else ""
    return REVIEW_PROMPT.format(
        pr_contexts=_build_pr_contexts(pr_record),
        fix_patch=pr_record.get("fix_patch", ""),
        rtl_code=rtl_code,
        generated_spec=spec_record["spec"],
    )


def _normalize_review(result: dict) -> dict:
    """Validate and normalize the LLM review response."""
    passed = bool(result.get("passed", False))
    checks = result.get("checks", {}) or {}
    return {
        "passed": passed,
        "checks": {
            "describes_correct_behavior": bool(
                checks.get("describes_correct_behavior", False)
            ),
            "no_patch_leakage": bool(checks.get("no_patch_leakage", False)),
            "sufficient_for_diagnosis": bool(
                checks.get("sufficient_for_diagnosis", False)
            ),
        },
        "reasoning": str(result.get("reasoning", "")),
    }


def process_review_specs(
    specs_file: Path,
    s1_6_file: Path,
    materials_file: Path,
    output_file: Path,
    failed_llm_file: Path,
    llm_client: LLMClient,
    skip_existing: bool,
    num_workers: int,
) -> None:
    """Review all generated specs."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    failed_llm_file.parent.mkdir(parents=True, exist_ok=True)

    existing: set[tuple[str, int]] = set()
    if skip_existing and output_file.exists():
        for rec in read_jsonl(output_file):
            existing.add(_record_key(rec))

    pr_records_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(s1_6_file):
        pr_records_by_key[_record_key(rec)] = rec

    materials_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(materials_file):
        materials_by_key[_record_key(rec)] = rec

    spec_records = [
        rec for rec in read_jsonl(specs_file) if _record_key(rec) not in existing
    ]

    mode = "a" if skip_existing else "w"
    write_lock = threading.Lock()

    def _worker(spec_record: dict) -> tuple[str, dict]:
        key = _record_key(spec_record)
        pr_record = pr_records_by_key.get(key)
        material = materials_by_key.get(key)
        if pr_record is None:
            return (
                "llm",
                {
                    "repo": spec_record["repo"],
                    "pr_id": spec_record["pr_id"],
                    "error": "Missing S1.6 PR record",
                },
            )
        try:
            prompt = build_prompt(pr_record, material, spec_record)
            result = llm_client.call_json(prompt)
            review = _normalize_review(result)
            return (
                "ok",
                {
                    "repo": spec_record["repo"],
                    "pr_id": spec_record["pr_id"],
                    **review,
                },
            )
        except Exception as exc:
            return (
                "llm",
                {
                    "repo": spec_record["repo"],
                    "pr_id": spec_record["pr_id"],
                    "error": str(exc),
                },
            )

    with (
        open(output_file, mode, encoding="utf-8") as out_f,
        open(failed_llm_file, mode, encoding="utf-8") as fail_f,
    ):
        if num_workers == 1:
            for spec_record in tqdm(spec_records, desc="S2.4 review specs"):
                status, payload = _worker(spec_record)
                if status == "ok":
                    write_jsonl_line(out_f, payload)
                else:
                    write_jsonl_line(fail_f, payload)
        else:
            with ThreadPoolExecutor(
                max_workers=num_workers, thread_name_prefix="S24-"
            ) as executor:
                future_to_spec = {
                    executor.submit(_worker, s): s for s in spec_records
                }
                for future in tqdm(
                    as_completed(future_to_spec),
                    total=len(spec_records),
                    desc="S2.4 review specs",
                ):
                    status, payload = future.result()
                    with write_lock:
                        if status == "ok":
                            write_jsonl_line(out_f, payload)
                        else:
                            write_jsonl_line(fail_f, payload)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="S2.4: review generated specs with an LLM."
    )
    parser.add_argument(
        "--specs_file",
        type=Path,
        required=True,
        help="Path to s2_03_generated_specs.jsonl.",
    )
    parser.add_argument(
        "--s1_6_file",
        type=Path,
        required=True,
        help="Path to the original S1.6 single-RTL candidates JSONL.",
    )
    parser.add_argument(
        "--materials_file",
        type=Path,
        required=True,
        help="Path to s2_01_materials.jsonl.",
    )
    parser.add_argument(
        "--out_dir", type=Path, required=True, help="Directory for S2 outputs."
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
        help="Skip PRs already present in the review-results output.",
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

    llm_client = LLMClient(
        base_url=args.llm_base_url,
        model=args.llm_model,
        api_key_env=args.llm_api_key_env,
    )

    output_name = _derive_output_name(
        args.specs_file.name, "_s2_04_review_results.jsonl"
    )
    output_file = args.out_dir / output_name
    failed_llm_file = args.out_dir / _derive_output_name(
        args.specs_file.name, "_failed_llm.jsonl"
    )

    process_review_specs(
        specs_file=args.specs_file,
        s1_6_file=args.s1_6_file,
        materials_file=args.materials_file,
        output_file=output_file,
        failed_llm_file=failed_llm_file,
        llm_client=llm_client,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
