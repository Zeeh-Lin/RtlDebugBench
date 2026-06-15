#!/usr/bin/env python3
"""Multi-repository wrapper for the RtlDebugBench S1 collection pipeline.

Runs `src/s1_collection/s1_pipeline.py` for a configurable list of GitHub
repositories. Repositories are processed sequentially by default; use
``--parallel`` to process them concurrently.

Example:
    python script/collect_repos.py \
        --tokens github-personal-access-token.txt \
        --out-dir datasets/collect \
        --skip-existing

Parallel example:
    python script/collect_repos.py \
        --tokens github-personal-access-token.txt \
        --parallel \
        --skip-existing
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


DEFAULT_REPOS = [
    ("chipsalliance", "caliptra-rtl"),
    ("lowRISC", "ibex"),
    ("openhwgroup", "cva6"),
]


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect S1 data from multiple RTL repositories."
    )
    parser.add_argument(
        "--tokens",
        type=str,
        default="github-personal-access-token.txt",
        help="GitHub API token(s) or path to token file (default: github-personal-access-token.txt).",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="datasets/collect",
        help="Root output directory (default: datasets/collect).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip steps whose output files already exist.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM issue extraction in S1.2, use regex only.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Process repositories in parallel (default: sequential).",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=None,
        help="Number of concurrent workers per pipeline step (passed to s1_pipeline.py).",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=1,
        choices=range(1, 7),
        help="Start pipeline from step N (1-6).",
    )
    parser.add_argument(
        "--repos",
        type=str,
        nargs="*",
        default=None,
        help="Override repository list with 'org/repo' strings.",
    )
    parser.add_argument(
        "--logs-dir",
        type=str,
        default="logs",
        help="Directory for per-repository log files (default: logs).",
    )
    return parser


def parse_repo_spec(spec: str) -> tuple[str, str]:
    """Parse 'org/repo' into (org, repo)."""
    if "/" not in spec:
        raise ValueError(f"Invalid repository spec '{spec}', expected 'org/repo'.")
    org, repo = spec.split("/", 1)
    return org.strip(), repo.strip()


def build_cmd(args, org: str, repo: str) -> list[str]:
    """Build the s1_pipeline.py command for one repository."""
    pipeline = Path("src/s1_collection/s1_pipeline.py")
    if not pipeline.exists():
        # Allow running from the repository root where the script lives.
        pipeline = Path(__file__).resolve().parent.parent / "src/s1_collection/s1_pipeline.py"

    cmd = [
        sys.executable,
        str(pipeline),
        "--org", org,
        "--repo", repo,
        "--tokens", args.tokens,
        "--out-dir", args.out_dir,
        "--start-from", str(args.start_from),
    ]
    if args.skip_existing:
        cmd.append("--skip-existing")
    if args.no_llm:
        cmd.append("--no-llm")
    if args.num_workers is not None:
        cmd.extend(["--num-workers", str(args.num_workers)])
    return cmd


def collect_one(args, org: str, repo: str, logs_dir: Path) -> tuple[str, str, int]:
    """Run the pipeline for a single repository."""
    spec = f"{org}/{repo}"
    print(f"[START] {spec}")

    log_file = logs_dir / f"{org}__{repo}.log"
    cmd = build_cmd(args, org, repo)

    with open(log_file, "w", encoding="utf-8") as log:
        result = subprocess.run(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

    status = "OK" if result.returncode == 0 else "FAILED"
    print(f"[{status}] {spec} (log: {log_file})")
    return org, repo, result.returncode


def main():
    parser = get_parser()
    args = parser.parse_args()

    # Resolve repositories.
    if args.repos:
        repos = [parse_repo_spec(spec) for spec in args.repos]
    else:
        repos = DEFAULT_REPOS

    # Validate token file exists.
    token_path = Path(args.tokens)
    if not token_path.exists():
        # The pipeline itself may accept a raw token string, so only warn.
        print(f"Warning: Token file '{args.tokens}' not found. Pipeline may fail if it is not a raw token.")

    # Prepare log directory.
    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Collecting data for {len(repos)} repository/repositories:")
    for org, repo in repos:
        print(f"  - {org}/{repo}")
    print(f"Output root: {out_dir.resolve()}")
    print(f"Logs: {logs_dir.resolve()}")
    print()

    results: list[tuple[str, str, int]] = []
    if args.parallel:
        with ThreadPoolExecutor(max_workers=len(repos)) as executor:
            future_to_repo = {
                executor.submit(collect_one, args, org, repo, logs_dir): (org, repo)
                for org, repo in repos
            }
            for future in as_completed(future_to_repo):
                results.append(future.result())
    else:
        for org, repo in repos:
            results.append(collect_one(args, org, repo, logs_dir))

    # Summary.
    failures = [(org, repo, rc) for org, repo, rc in results if rc != 0]
    print()
    print("=" * 60)
    print("Collection summary")
    print("=" * 60)
    print(f"Total:    {len(results)}")
    print(f"Success:  {len(results) - len(failures)}")
    print(f"Failures: {len(failures)}")
    if failures:
        print("\nFailed repositories:")
        for org, repo, rc in failures:
            print(f"  - {org}/{repo} (exit code {rc})")
        sys.exit(1)


if __name__ == "__main__":
    main()
