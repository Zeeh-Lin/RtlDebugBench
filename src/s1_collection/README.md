# `s1_collection/` — S1 PR data collection

This module implements RtlDebugBench **S1**: collect merged PRs from a GitHub
hardware repository, link them to resolved issues, extract their diffs, and
filter down to PRs that modify **exactly one RTL source file**.

The output is consumed directly by S2 (input-package / spec generation).

## Pipeline shape

```
s1_01_fetch_prs ──► s1_02_filter_by_issues ──► s1_03_fetch_issues
  GitHub API              local (+LLM)            GitHub API
        │                                              │
        ▼                                              ▼
s1_04_merge ◄──────────────────────────────── s1_05_extract_patches
  local (join)                                   GitHub API
        │
        ▼
s1_06_filter_patches
  local (single-RTL filter)
```

All outputs land under `datasets/collect/<ORG>/<ORG>__<REPO>_s01_NN_*.jsonl`.
Each stage consumes the previous stage's JSONL, so the pipeline is a straight
chain with no branching.

## Quick start

Run the whole pipeline end-to-end against a single repo:

```bash
python src/s1_collection/s1_pipeline.py \
  --org lowRISC \
  --repo ibex \
  --tokens tokens.txt \
  --out-dir datasets/collect \
  --skip-existing
```

Put API keys in a `.env` file at the project root:

```bash
DEEPSEEK_API_KEY=...
GITHUB_TOKEN=...
```

`python-dotenv` loads them automatically. `--tokens` accepts either a path to a
token file (one token per line) or multiple values inline
(`--tokens tok1 tok2 tok3`). `--skip-existing` makes each stage a no-op if its
output file already exists, which is how you resume after an interrupted run.
To start part-way through, use `--start-from N` where `N` is the step number
(1–6).

### LLM issue extraction (S1.2)

By default S1.2 uses a hosted LLM (DeepSeek-reasoner) to extract resolved-issue
references from PR titles, bodies, and commit messages. Disable it with
`--no-llm` to fall back to regex-only extraction. Override the endpoint with:

```bash
--llm-base-url https://api.deepseek.com \
--llm-model deepseek-reasoner \
--llm-api-key-env DEEPSEEK_API_KEY
```

## Per-stage reference

### S1.1 — `s1_01_fetch_prs`

*Input: none. Output: `<ORG>__<REPO>_s01_01_prs.jsonl`.*

Paginates `get_pulls(state="all")` across the available tokens and records every
PR's metadata. For merged PRs it additionally fetches the commit list so that
S1.2 can scan commit messages locally without another round-trip. Writes are
incremental, keyed by PR `id` for dedup on resume.

### S1.2 — `s1_02_filter_by_issues`

*Input: S1.1 output. Output: `<ORG>__<REPO>_s01_02_issue_linked_prs.jsonl`.*

Local-only stage. Keeps only closed+merged PRs, then extracts referenced issue
numbers through one of two paths. The regex path scans title, body, and commit
messages for `(fixes|closes|resolves) #N`-style references. The LLM path sends
the same text to a DeepSeek-reasoner class model and asks for a JSON array of
issue numbers, which catches unconventional references. PRs with no extracted
references are dropped.

### S1.3 — `s1_03_fetch_issues`

*Input: S1.2 output. Output: `<ORG>__<REPO>_s01_03_issues.jsonl`.*

Unions the issue numbers referenced across S1.2's surviving PRs and fetches each
one's title, body, and state via `get_issue(N)`. Token-sharded, thread-pooled.

### S1.4 — `s1_04_merge`

*Input: S1.2 and S1.3 outputs. Output: `<ORG>__<REPO>_s01_04_merged_prs.jsonl`.*

Pure local join. For each PR in S1.2, look up each of its referenced issue
numbers in S1.3 and replace the integer list with a list of issue dicts
(`number`, `title`, `body`, `state`).

### S1.5 — `s1_05_extract_patches`

*Input: S1.4 output. Output: `<ORG>__<REPO>_s01_05_patches.jsonl`.*

For each PR, calls
`GET /repos/<ORG>/<REPO>/compare/<base.sha>...<head.sha>` with
`Accept: application/vnd.github.v3.diff` to retrieve the full diff. The diff is
split by file path into `fix_patch` (design and source hunks) and `test_patch`
(any hunk whose path contains a test keyword). Populates `modified_files`,
`lines_added`, and `lines_removed`. PRs whose `fix_patch` is empty are dropped.

### S1.6 — `s1_06_filter_patches`

*Input: S1.5 output. Output: `<ORG>__<REPO>_s01_06_single_rtl_candidates.jsonl`.*

Local filter. Keeps only PRs that modify **exactly one RTL source file**,
where RTL means extension `.v`, `.sv`, or `.svh` and the file path is **not**
under a test directory (`test`, `tests`, `e2e`, `testing`, `tb`, `tbs`,
`testbench`). There is no limit on patch size or number of modified test files.

Each surviving record is reshaped to the S1 output schema:

- `repo`: `org/repo`
- `pr_id`: PR number
- `lang`: `"v"` for `.v`, `"sv"` for `.sv`/`.svh`
- `rtl_files`: `["rtl/..."]` — exactly one path
- `commit_id`: `pull.base.sha`
- `timestamp`: `merged_at`
- Auxiliary context: `pr_title`, `pr_body`, `issue_title`, `issue_body`,
  `commit_message`, `fix_patch`, `test_patch`, `modified_files`,
  `lines_added`, `lines_removed`

## Running a single stage

Each stage can also be run standalone for debugging or partial reruns:

```bash
python src/s1_collection/s1_01_fetch_prs.py \
  --out_dir datasets/collect/lowRISC \
  --tokens tokens.txt \
  --org lowRISC --repo ibex
```

Use `--help` on any stage to see its flags. Several stages parse `org` and
`repo` back out of the input filename, so filenames must follow the
`<ORG>__<REPO>_s01_NN_*.jsonl` convention.

## Data contract

A PR record accumulates fields as it moves through the pipeline:

- **After S1.1:** PR metadata — `org`, `repo`, `number`, `state`, `title`,
  `body`, `base`, created / updated / closed / merged timestamps, `labels`,
  `commits`.
- **After S1.2:** `resolved_issues` as a list of integer issue numbers.
- **After S1.3 + S1.4:** `resolved_issues` replaced by a list of issue dicts
  (`number`, `title`, `body`, `state`).
- **After S1.5:** `fix_patch`, `test_patch`, `modified_files`, `lines_added`,
  `lines_removed`.
- **After S1.6:** reshaped to the final S1 candidate schema described above.

The S1.6 JSONL is the input to RtlDebugBench S2.
