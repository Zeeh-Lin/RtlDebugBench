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

"""S2.3: Generate a behavioral design spec from fixed RTL and selected documents."""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

# Allow direct execution: python src/s2_input_construction/s2_03_generate_spec.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from s2_input_construction.llm_client import LLMClient
from s2_input_construction.parsers import format_official_spec
from s2_input_construction.s2_01_fetch_materials import (
    _derive_output_name,
    _record_key,
    read_jsonl,
    write_jsonl_line,
)


SPEC_GENERATION_PROMPT = """You are a senior RTL architect. Generate a design specification (Spec) for the module described below.

## Module Info
- Repository: {repo}
- Language: {lang}
- RTL Files: {rtl_files}

## Input Materials
### RTL Source
```systemverilog
{rtl_code}
```

### Official Specification Documents
{official_spec}

## Task
Write a behavioral design specification (Spec) for this RTL module.
The Spec should describe WHAT the module must do under various conditions, not HOW it is implemented.
Use Markdown in a natural, free-form structure that best conveys the module's behavior.

## Rules
1. Describe correct behavior (WHAT), not implementation details (HOW).
2. Do NOT mention signal names, variable names, line numbers, code structure, fix methods, or test methods.
3. Use normative language where appropriate: must, shall, should.
4. Self-contained: an engineer unfamiliar with the project should understand what the module should do.
5. Do NOT use words that imply bugs or fixes: incorrectly, broken, fixed, bug, patched, repaired.
6. Keep the spec focused on observable behavior and functional requirements.
"""


def build_prompt(material: dict, selected: dict) -> str:
    """Build the spec-generation prompt for a single PR."""
    official_spec = format_official_spec(
        selected.get("selected_doc_paths", []),
        material.get("doc_full_texts", {}),
    )
    return SPEC_GENERATION_PROMPT.format(
        repo=material["repo"],
        lang=material.get("lang", "sv"),
        rtl_files=json.dumps(material.get("rtl_files", [material["rtl_file"]]), ensure_ascii=False),
        rtl_code=material["fixed_rtl_code"],
        official_spec=official_spec,
    )


def process_generate_specs(
    materials_file: Path,
    selected_docs_file: Path,
    output_file: Path,
    failed_llm_file: Path,
    llm_client: LLMClient,
    skip_existing: bool,
    num_workers: int,
) -> None:
    """Generate specs for all selected-doc records."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    failed_llm_file.parent.mkdir(parents=True, exist_ok=True)

    existing: set[tuple[str, int]] = set()
    if skip_existing and output_file.exists():
        for rec in read_jsonl(output_file):
            existing.add(_record_key(rec))

    materials_by_key: dict[tuple[str, int], dict] = {}
    for rec in read_jsonl(materials_file):
        materials_by_key[_record_key(rec)] = rec

    selected_records = [
        rec
        for rec in read_jsonl(selected_docs_file)
        if _record_key(rec) not in existing
    ]

    mode = "a" if skip_existing else "w"
    write_lock = threading.Lock()

    def _worker(selected: dict) -> tuple[str, dict]:
        key = _record_key(selected)
        material = materials_by_key.get(key)
        if material is None:
            return (
                "llm",
                {
                    "repo": selected["repo"],
                    "pr_id": selected["pr_id"],
                    "error": "Missing materials record",
                },
            )
        try:
            prompt = build_prompt(material, selected)
            spec_text = llm_client.call(prompt)
            return (
                "ok",
                {
                    "repo": selected["repo"],
                    "pr_id": selected["pr_id"],
                    "spec": spec_text,
                },
            )
        except Exception as exc:
            return (
                "llm",
                {
                    "repo": selected["repo"],
                    "pr_id": selected["pr_id"],
                    "error": str(exc),
                },
            )

    with (
        open(output_file, mode, encoding="utf-8") as out_f,
        open(failed_llm_file, mode, encoding="utf-8") as fail_f,
    ):
        if num_workers == 1:
            for selected in tqdm(selected_records, desc="S2.3 generate specs"):
                status, payload = _worker(selected)
                if status == "ok":
                    write_jsonl_line(out_f, payload)
                else:
                    write_jsonl_line(fail_f, payload)
        else:
            with ThreadPoolExecutor(
                max_workers=num_workers, thread_name_prefix="S23-"
            ) as executor:
                future_to_selected = {
                    executor.submit(_worker, s): s for s in selected_records
                }
                for future in tqdm(
                    as_completed(future_to_selected),
                    total=len(selected_records),
                    desc="S2.3 generate specs",
                ):
                    status, payload = future.result()
                    with write_lock:
                        if status == "ok":
                            write_jsonl_line(out_f, payload)
                        else:
                            write_jsonl_line(fail_f, payload)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="S2.3: generate behavioral specs from fixed RTL and selected docs."
    )
    parser.add_argument(
        "--materials_file",
        type=Path,
        required=True,
        help="Path to s2_01_materials.jsonl.",
    )
    parser.add_argument(
        "--selected_docs_file",
        type=Path,
        required=True,
        help="Path to s2_02_selected_docs.jsonl.",
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
        help="Skip PRs already present in the generated-specs output.",
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
        args.materials_file.name, "_s2_03_generated_specs.jsonl"
    )
    output_file = args.out_dir / output_name
    failed_llm_file = args.out_dir / _derive_output_name(
        args.materials_file.name, "_failed_llm.jsonl"
    )

    process_generate_specs(
        materials_file=args.materials_file,
        selected_docs_file=args.selected_docs_file,
        output_file=output_file,
        failed_llm_file=failed_llm_file,
        llm_client=llm_client,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
