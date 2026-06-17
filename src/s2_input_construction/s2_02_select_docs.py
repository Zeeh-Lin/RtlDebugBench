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

"""S2.2: Use an LLM to pre-select official documents relevant to each RTL module."""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

# Allow direct execution: python src/s2_input_construction/s2_02_select_docs.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from s2_input_construction.llm_client import LLMClient
from s2_input_construction.parsers import format_doc_toc
from s2_input_construction.s2_01_fetch_materials import (
    _derive_output_name,
    _record_key,
    read_jsonl,
    write_jsonl_line,
)


SELECT_DOCS_PROMPT = """You are an RTL documentation matching assistant.

## RTL Module
Repository: {repo}
RTL File: {rtl_file}

```systemverilog
{fixed_rtl_code}
```

## Available Official Documents
{doc_toc}

## Task
Select the official documents most relevant to the RTL module above.
Prefer documents whose content would help write a behavioral spec for this module.
If no document is relevant, return an empty list.

## Output
Output valid JSON only:
{{
  "selected_doc_paths": ["doc/03_reference/pmp.rst"],
  "reason": "one sentence explanation"
}}
"""


def build_prompt(material: dict) -> str:
    """Build the document-selection prompt for a single PR."""
    return SELECT_DOCS_PROMPT.format(
        repo=material["repo"],
        rtl_file=material["rtl_file"],
        fixed_rtl_code=material["fixed_rtl_code"],
        doc_toc=format_doc_toc(material["doc_toc"]),
    )


def _normalize_paths(paths: Any, available_docs: set[str]) -> list[str]:
    """Ensure selected_doc_paths is a deduplicated list of existing doc paths."""
    if not isinstance(paths, list):
        return []
    seen: set[str] = set()
    result = []
    for p in paths:
        if isinstance(p, str) and p in available_docs and p not in seen:
            seen.add(p)
            result.append(p)
    return result


def process_select_docs(
    materials_file: Path,
    output_file: Path,
    failed_llm_file: Path,
    llm_client: LLMClient,
    skip_existing: bool,
    num_workers: int,
) -> None:
    """Run doc selection for every material record."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    failed_llm_file.parent.mkdir(parents=True, exist_ok=True)

    existing: set[tuple[str, int]] = set()
    if skip_existing and output_file.exists():
        for rec in read_jsonl(output_file):
            existing.add(_record_key(rec))

    materials = [
        rec for rec in read_jsonl(materials_file) if _record_key(rec) not in existing
    ]

    mode = "a" if skip_existing else "w"
    write_lock = threading.Lock()

    def _worker(material: dict) -> tuple[str, dict]:
        repo = material["repo"]
        pr_id = material["pr_id"]
        available_docs = set(material.get("doc_full_texts", {}).keys())
        try:
            prompt = build_prompt(material)
            result = llm_client.call_json(prompt)
            selected_paths = _normalize_paths(
                result.get("selected_doc_paths", []), available_docs
            )
            reason = result.get("reason", "")
            payload = {
                "repo": repo,
                "pr_id": pr_id,
                "selected_doc_paths": selected_paths,
                "reason": reason,
            }
            return ("ok", payload)
        except Exception as exc:
            return (
                "llm",
                {
                    "repo": repo,
                    "pr_id": pr_id,
                    "error": str(exc),
                },
            )

    with (
        open(output_file, mode, encoding="utf-8") as out_f,
        open(failed_llm_file, mode, encoding="utf-8") as fail_f,
    ):
        if num_workers == 1:
            for material in tqdm(materials, desc="S2.2 select docs"):
                status, payload = _worker(material)
                if status == "ok":
                    write_jsonl_line(out_f, payload)
                else:
                    write_jsonl_line(fail_f, payload)
        else:
            with ThreadPoolExecutor(
                max_workers=num_workers, thread_name_prefix="S22-"
            ) as executor:
                future_to_material = {
                    executor.submit(_worker, m): m for m in materials
                }
                for future in tqdm(
                    as_completed(future_to_material),
                    total=len(materials),
                    desc="S2.2 select docs",
                ):
                    status, payload = future.result()
                    with write_lock:
                        if status == "ok":
                            write_jsonl_line(out_f, payload)
                        else:
                            write_jsonl_line(fail_f, payload)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="S2.2: select relevant official documents with an LLM."
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
        help="Skip PRs already present in the selected-docs output.",
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
        args.materials_file.name, "_s2_02_selected_docs.jsonl"
    )
    output_file = args.out_dir / output_name
    failed_llm_file = args.out_dir / _derive_output_name(
        args.materials_file.name, "_failed_llm.jsonl"
    )

    process_select_docs(
        materials_file=args.materials_file,
        output_file=output_file,
        failed_llm_file=failed_llm_file,
        llm_client=llm_client,
        skip_existing=args.skip_existing,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
