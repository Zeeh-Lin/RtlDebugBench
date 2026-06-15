#!/usr/bin/env python3
"""
RtlDebugBench S1 data-collection pipeline.

Runs S1.1 → S1.6:
  1. Fetch all PRs (with commits).
  2. Filter to merged PRs linked to resolved issues (local + optional LLM).
  3. Fetch linked issue details.
  4. Merge PR and issue data.
  5. Extract unified diff and split into fix_patch / test_patch.
  6. Keep only PRs that modify exactly one RTL source file.

All intermediate results are written as JSONL files under --out-dir so the
pipeline can be resumed with --start-from and --skip-existing.
"""

import argparse
import os
import sys
from pathlib import Path

# Allow direct execution: python src/s1_collection/s1_pipeline.py
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()


def run_step(description: str, func, *args, **kwargs):
    """Run a pipeline step with logging."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"{'='*60}")
    func(*args, **kwargs)
    print(f"Done: {description}")


def main():
    parser = argparse.ArgumentParser(description="RtlDebugBench S1 data collection")
    parser.add_argument("--org", type=str, required=True, help="GitHub organization")
    parser.add_argument("--repo", type=str, required=True, help="GitHub repository")
    parser.add_argument(
        "--out-dir",
        type=str,
        default="datasets/collect",
        help="Output directory relative to project root (default: datasets/collect)",
    )
    parser.add_argument(
        "--tokens",
        type=str,
        nargs="*",
        default=None,
        help="GitHub API token(s) or path to token file",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=1,
        choices=range(1, 7),
        help="Start from step N (1-6)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip steps whose output files already exist",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM issue extraction in S1.2, use regex only",
    )
    parser.add_argument(
        "--llm-base-url",
        type=str,
        default=None,
        help="OpenAI-compatible API base URL for LLM issue extraction (default: https://api.deepseek.com)",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="Model name for LLM issue extraction (default: deepseek-reasoner)",
    )
    parser.add_argument(
        "--llm-api-key-env",
        type=str,
        default=None,
        help="Environment variable name for LLM API key (default: DEEPSEEK_API_KEY)",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=min(32, os.cpu_count() + 4 if os.cpu_count() else 10),
        help="Number of concurrent workers",
    )

    args = parser.parse_args()

    from s1_collection.util import get_tokens

    tokens = get_tokens(args.tokens)

    out_dir = Path(args.out_dir) / args.org
    out_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{args.org}__{args.repo}"

    print(f"\nCollecting data for {args.org}/{args.repo}")
    print(f"Output: {out_dir}")
    if args.start_from > 1:
        print(f"Starting from step {args.start_from}")

    # ── S1.1: Fetch all PRs ────────────────────────────────────
    prs_file = out_dir / f"{base_name}_s01_01_prs.jsonl"
    if args.start_from <= 1:
        if args.skip_existing and prs_file.exists():
            print(f"\nSkip S1.1: {prs_file} exists")
        else:
            from s1_collection.s1_01_fetch_prs import main as s1_main
            run_step(
                "S1.1: Fetch all PRs",
                s1_main, tokens, out_dir, args.org, args.repo, args.num_workers,
            )

    # ── S1.2: Filter by resolved issues ────────────────────────
    filtered_prs_file = out_dir / f"{base_name}_s01_02_issue_linked_prs.jsonl"
    if args.start_from <= 2:
        if args.skip_existing and filtered_prs_file.exists():
            print(f"\nSkip S1.2: {filtered_prs_file} exists")
        else:
            from s1_collection.s1_02_filter_by_issues import main as s2_main
            run_step(
                "S1.2: Filter PRs by resolved issues",
                s2_main,
                out_dir,
                prs_file,
                args.num_workers,
                use_llm=not args.no_llm,
                llm_base_url=args.llm_base_url or "https://api.deepseek.com",
                llm_model=args.llm_model or "deepseek-reasoner",
                llm_api_key_env=args.llm_api_key_env or "DEEPSEEK_API_KEY",
            )

    # ── S1.3: Fetch related issue details ──────────────────────
    issues_file = out_dir / f"{base_name}_s01_03_issues.jsonl"
    if args.start_from <= 3:
        if args.skip_existing and issues_file.exists():
            print(f"\nSkip S1.3: {issues_file} exists")
        else:
            from s1_collection.s1_03_fetch_issues import main as s3_main
            run_step(
                "S1.3: Fetch related issue details",
                s3_main, tokens, out_dir, filtered_prs_file, args.num_workers,
            )

    # ── S1.4: Merge PRs with issues ────────────────────────────
    merged_file = out_dir / f"{base_name}_s01_04_merged_prs.jsonl"
    if args.start_from <= 4:
        if args.skip_existing and merged_file.exists():
            print(f"\nSkip S1.4: {merged_file} exists")
        else:
            from s1_collection.s1_04_merge import main as s4_main
            run_step(
                "S1.4: Merge PRs with issue data",
                s4_main, out_dir, args.org, args.repo,
            )

    # ── S1.5: Extract and split patches ────────────────────────
    patches_file = out_dir / f"{base_name}_s01_05_patches.jsonl"
    if args.start_from <= 5:
        if args.skip_existing and patches_file.exists():
            print(f"\nSkip S1.5: {patches_file} exists")
        else:
            from s1_collection.s1_05_extract_patches import main as s5_main
            run_step(
                "S1.5: Extract and split patches",
                s5_main,
                tokens=tokens,
                out_dir=out_dir,
                filtered_prs_with_issues_file=merged_file,
                delay_on_error=300,
                retry_attempts=3,
                num_workers=args.num_workers,
            )

    # ── S1.6: Keep only single-RTL-file PRs ────────────────────
    candidates_file = out_dir / f"{base_name}_s01_06_single_rtl_candidates.jsonl"
    if args.start_from <= 6:
        if args.skip_existing and candidates_file.exists():
            print(f"\nSkip S1.6: {candidates_file} exists")
        else:
            from s1_collection.s1_06_filter_patches import main as s6_main
            run_step(
                "S1.6: Filter to single-RTL-file candidates",
                s6_main, patches_file, out_dir, args.num_workers,
            )

    print(f"\n{'='*60}")
    print("S1 collection complete.")
    print(f"\nOutput files in: {out_dir}")
    print(f"  - S1.1 Raw PRs:               {base_name}_s01_01_prs.jsonl")
    print(f"  - S1.2 Issue-linked PRs:      {base_name}_s01_02_issue_linked_prs.jsonl")
    print(f"  - S1.3 Related issues:        {base_name}_s01_03_issues.jsonl")
    print(f"  - S1.4 Merged PRs:            {base_name}_s01_04_merged_prs.jsonl")
    print(f"  - S1.5 Patches:               {base_name}_s01_05_patches.jsonl")
    print(f"  - S1.6 Single-RTL candidates: {base_name}_s01_06_single_rtl_candidates.jsonl")


if __name__ == "__main__":
    main()
