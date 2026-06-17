# Benchmark信息
输入\&输出格式

> **以josnl文件组织，每一行是一个json （参考下HWE\-bench格式）**
> 
> 



```JSON
{
    input: {},
    bug_info: [],
    aux: {}   // S2/S3 辅助字段，非评测必填
}
```

## 输入

```JSON
input: {
    repo: repo_id,
    pr_id: pr_id,
    lang: v/sv/chiesl,
    rtl_files: [file paths],
    spec: 字符串描述，
    commit_id: 四十位的完整hash,  //代码fix之前（PR merge之前）获取的commit ID
    timestamp: 时间戳
}
```

示例：

```JSON
"input": {
    "repo": "lowRISC/ibex",
    "pr_id":"1234"
    "lang": "sv",
    "rtl_files": ["rtl/ibex_pmp.sv"],
    "spec": "PMP (Physical Memory Protection) unit implementing RISC-V privileged spec v1.11",
    "commit_id": "abc123def456",
    "timestamp": "2026-05-15T10:30:00Z"
}
```

## 输出

```JSON
// 暂时先考虑单文件情况，多文件可用二维列表
// 以下是单文件，1或多个Bug
"bug_info": [
  // 单文件中 bug 1
  {
    "bug_lineno": [
      开始行1, 结束行1,
      开始行2, 结束行2
    ],
    "bug_type": {
      "level1": "Functional Bug",
      "level2": "Boundary Condition Bug"
    },
    "bug_desc": *string*,
    "fix_hint": *string*
  },
  // 单文件中 bug 2
  {
    "bug_lineno": [
      开始行1, 结束行1,
      开始行2, 结束行2
    ],
    "bug_type": {
      "level1": "Functional Bug",
      "level2": "Boundary Condition Bug"
    },
    "bug_desc": *string*,
    "fix_hint": *string*
  },
  *...*
  *...*
  *...*
]
```



示例：

```JSON
"bug_info": [
  {
    "bug_lineno": [
      407,
      407
    ],
    "bug_type": {
      "level1": "Functional Bug",
      "level2": "Control Flow Bug"
    },
    "bug_desc": "The DMA controller can decide that a transfer is complete before the AES FSM has reached a terminal or inactive state. This is exposed by a 1 DWORD DMA/AES transfer, where byte-transfer bookkeeping can finish before AES has completed, causing the DMA FSM to move to DONE too early.",
    "fix_hint": "Require the DMA completion predicate to also confirm that the AES FSM is idle, done, or in error before allowing the DMA FSM to treat all bytes as transferred."
  }
]
```

# 构建流程（Repo PR采集\&筛选）

- **S1 数据采集**：从目标仓库（如 lowRISC/ibex）通过 GitHub API 拉取已合并的 bug 修复 PR，并过滤为「仅修改单个RTL文件」的候选集。分为以下几个子步骤：

    1. **S1\.1 拉取所有 PR**：通过 GitHub API 拉取目标仓库的全部 PR，并为每个 PR 拉取其 commit 列表。输出 `s01_01_prs.jsonl`。

    2. **S1\.2 按 issue 过滤**：用 LLM 从 PR title、PR body 以及所有 commit messages 中提取关联 issue 号；仅保留已合并（closed \&\& merged\_at 非空）且成功解析出 issue 号的 PR。输出 `s01_02_issue_linked_prs.jsonl`。

    3. **S1\.3 拉取 issue 详情**：根据 S1\.2 得到的 issue 号列表，拉取每个 issue 的 title 和 body。输出 `s01_03_issues.jsonl`。

    4. **S1\.4 合并 PR 与 issue 数据**：将 S1\.2 的 PR 记录与 S1\.3 的 issue 记录按 issue 号合并。输出 `s01_04_merged_prs.jsonl`。

    5. **S1\.5 抽取 patch**：通过 GitHub compare API 获取 base commit 到 head commit 的完整 unified diff，并按文件路径拆分为 `fix_patch`（非测试文件）和 `test_patch`（测试相关文件）。输出 `s01_05_patches.jsonl`。（以上HWE流程）

    6. **S1\.6 patch 粗筛与单 RTL 文件过滤**：仅保留 `fix_patch` 中恰好修改了 1 个 RTL 文件（`.v` / `.sv` / `.svh`）的 PR；单个文件修改长度不限；丢弃测试目录、markdown、多 RTL 文件修改等。输出 `s01_06_single_rtl_candidates.jsonl`。输出除 input 所需字段外，还保留 `merge_commit_sha`、PR title/body、issue title/body、commit message、`fix_patch`、`test_patch` 等辅助字段，供 S2/S3 使用。

    - 仓库链接：https://github\.com/Zeeh\-Lin/RtlDebugBench

- **S****2**** 输入包构造**：基于 S1\.6 候选，生成每个 PR 的 input 字段与辅助字段。

    - repo、pr\_id、lang、rtl\_files、commit\_id、timestamp 等字段直接取自 PR metadata；其中 `commit_id` 指向 buggy base commit，`merge_commit_sha` 指向修复后的 merge commit。
    - spec 由 LLM 从修复后的 RTL 源码和官方文档生成，经审查后写入 input。
    - 未通过审查的 spec 进入人工审查队列，由人工决定修改或丢弃。
    - 最终输出 `s02_inputs.jsonl`，每行包含 `input`、`bug_info`（S2 阶段为空列表）以及 `aux` 辅助字段。

- **S****3**** 输出包构造****\(****bug\_lineno、bug\_type、****bug\_desc、fix\_hint ****\)**：

    - **bug\_lineno：通过确定性算法（非LLM\-based）可以确定，后面章节会讲具体方法。**

    - **bug\_type、bug\_desc、fix\_hint：需要LLM来生成，后面章节会讲具体方法。**

# 输入字段构建

## Spec 生成

Spec 是模块功能规格描述，作为 LLM 审查代码的基准。LLM 需对照 spec 检查 RTL 代码行为是否一致。Spec 只描述**正确行为应该是什么**（WHAT），不描述实现方式（HOW），不泄露 bug 位置。

生成 Spec 需满足以下原则：

- **行为级描述**：只描述可观测的正确行为，不描述内部实现。
- **自包含**：不熟悉该项目的工程师阅读后应能理解模块应该做什么。
- **防泄露**：不提及信号名、行号、代码结构、修复方法、测试方法。

### S2 Pipeline

S2 将 S1.6 的每个候选 PR 转换为完整的输入包，分为四个子步骤：

1. **s2_01_fetch_materials**：本地 clone 仓库，checkout 到 `merge_commit_sha`，读取修复后的 RTL 源码和官方文档目录。
2. **s2_02_select_docs**：解析每篇文档标题生成 TOC，用 LLM 从 TOC 中预筛选与当前 RTL 模块相关的文档。
3. **s2_03_generate_spec**：用修复后的 RTL 源码 + 选中的官方文档生成 spec，生成阶段不引入 PR title/body/issue/commit message 等可能泄露 bug/fix 的信息。
4. **s2_04_review_spec**：用 PR 证据和 `fix_patch` 审查生成的 spec；通过则进入 `s02_inputs.jsonl`，未通过则进入 `manual_review_queue.jsonl` 由人工处理。

### 官方文档获取

目标仓库都有官方文档目录：

- `lowRISC/ibex`：`doc/`
- `chipsalliance/caliptra-rtl`：`docs/`
- `openhwgroup/cva6`：`docs/`

S2 采用 repo-specific 配置指定文档目录和 RTL 目录，脚本本地 clone 后按配置读取。如果某个 PR 未筛选出相关文档，官方 spec 部分留空，spec 生成仍基于修复后的 RTL 继续进行。

### 文档预筛选Prompt

```Markdown
You are an RTL documentation matching assistant.

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
{
  "selected_doc_paths": ["doc/03_reference/pmp.rst"],
  "reason": "one sentence explanation"
}
```

### 生成策略

生成阶段输入材料包括：

- 修复后的完整 RTL 文件（来自 `merge_commit_sha`）。
- 选中的官方文档全文（来自 s2_02_select_docs；若未选中则为空）。

不引入 PR title/body/issue/commit message，避免 bug/fix 细节泄露到 spec 中。

### 生成流程（伪代码）

```Python
def build_s2_input(pr_record: dict) -> dict:
    materials = fetch_materials(pr_record)          # s2_01
    selected_docs = select_docs(materials)          # s2_02
    spec_text = generate_spec(materials, selected_docs)  # s2_03
    review = review_spec(spec_text, materials)      # s2_04

    if not review["passed"]:
        write_manual_review_queue(pr_record, spec_text, review)
        return None

    return {
        "input": {
            "repo": pr_record["repo"],
            "pr_id": pr_record["pr_id"],
            "lang": pr_record["lang"],
            "rtl_files": pr_record["rtl_files"],
            "spec": spec_text,
            "commit_id": pr_record["commit_id"],
            "timestamp": pr_record["timestamp"],
        },
        "bug_info": [],
        "aux": {
            "merge_commit_sha": pr_record["merge_commit_sha"],
            "pr_title": pr_record["pr_title"],
            "pr_body": pr_record["pr_body"],
            "issue_title": pr_record["issue_title"],
            "issue_body": pr_record["issue_body"],
            "commit_message": pr_record["commit_message"],
            "fix_patch": pr_record["fix_patch"],
            "fixed_rtl_code": materials["fixed_rtl_code"],
            "selected_doc_paths": selected_docs,
        },
    }
```

### 审查失败处理

审查未通过时，不自动让 LLM 改写，而是将以下信息写入 `manual_review_queue.jsonl`：

- PR 标识（repo、pr_id）
- 审查结果（未通过的 check 和原因）
- 生成的 spec 原文
- fixed RTL 源码
- PR title/body 和 fix_patch

人工审查后决定修改 spec 重新进入 review，或丢弃该 PR。

### 生成Prompt（需要与3\.1\.2保持一致）

```Markdown
You are a senior RTL architect. Generate a design specification (Spec) for the module described below.

## Module Info
- Repository: {repo}
- Language: {lang}
- RTL Files: {rtl_files}

## Input Materials
### RTL Source
{RTL Code}

### Official Specification Documents
{Official Spec}

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
```

### 审查Prompt

```Markdown
You are a senior RTL verification engineer. Review the following Spec for quality.

## Input Materials

### PR Title & Body
{PR Contexts}

### Fix Patch
{Fix Patch}

### RTL Source
{RTL Code}

### Generated Specification
```markdown
{generated spec}
```

## Checks (true / false)
1. describes_correct_behavior: Does the **generated specification** use must/shall/should to describe correct behavior? No "bug/fixed/broken/incorrectly".
2. no_patch_leakage: Does the **generated specification** contain NO signal names, variable names, assign expressions, or line numbers from the patch?
3. sufficient_for_diagnosis: Does the generated specification contain at least one behavioral assertion directly related to the functional area modified by the patch?

## Output
Output valid JSON only, no markdown fences:
{"passed": true, "checks": {"describes_correct_behavior": true, "no_patch_leakage": true, "sufficient_for_diagnosis": true}, "reasoning": "one sentence summary"}
```

# 输出字段构建

## bug\_lineno 

LLM辅助过滤后再经过人工审查resaon，LLM的prompt如下，产出用于 root\-cause 人工审查标注的候选 hunk shortlist：

```Plain Text
You are a senior RTL root-cause hunk filtering assistant.

Filter the fix patch hunks and keep the hunks that may contain the root cause.
This step produces a hunk shortlist for human root-cause review and annotation.

## Case
Repository: {repo}
Language: {lang}

## Evidence
PR Title: {pr_title}
PR Body:
{pr_body}

Issue:
{issue_text}

Commit Message:
{commit_message}

## Buggy RTL Code Before Merge
The following contains the full pre-merge source code of all files that contain diff hunks in this PR.

{rtl_code} //单文件

## Fix Patch Hunks
The input fix_patch is already split into hunks with hunk_id.

Expected format:
[
  {"HUNK_ID": "HUNK_CODE"},
  {"HUNK_ID": "HUNK_CODE"}
]

{fix_patch}

## Output
Output valid JSON only. Use hunk_id values from Fix Patch Hunks.

{
  "candidate_root_cause_hunks": [
    {
      "hunk_ids": [
        "HUNK_1",
        "HUNK_2"
      ],
      "reason": "Explain why these hunks may contain the root cause."
    }
  ],
  "filtered_non_root_cause_hunks": [
    {
      "hunk_ids": [
        "HUNK_3",
        "HUNK_4"
      ],
      "reason": "Explain why these hunks are not likely to be root-cause hunks."
    }
  ]
}

## Rules
1. This step produces a hunk shortlist for human root-cause review and annotation.
2. Be conservative: keep any hunk that may contain the root cause.
3. Filter only hunks that are clearly formatting, comments, renaming, cleanup, tests, unrelated edits, or surrounding adaptation code.
```

### ~~问题定义~~

**~~输入~~**~~：~~

- ~~`fix_patch`~~~~：GitHub PR 的 unified diff（完整修复补丁）~~

- ~~`RTL 源码`~~~~：~~~~带\.git历史的完整Repo~~

- ~~`tb_script`~~~~：能复现 bug 并通过 fix 验证的测试脚本~~~~，~~~~HWE提供~~

**~~输出~~**~~：~~

~~\{~~

~~"files": \["rtl/ibex\_decoder\.sv"\],~~

~~"line\_ranges": \[\[422, 428\]\]~~

~~\}~~

> ~~`files`~~~~：根因所在文件。~~~~`line_ranges`~~~~：buggy 代码中的行号范围。~~
> 
> 

---

### ~~Patch hunk解析~~

~~RTL 文件过滤：仅保留 ~~~~`.sv`~~~~ ~~~~`.svh`~~~~ ~~~~`.v`~~~~ 文件，排除 ~~~~`/dv/`~~~~ ~~~~`/tb/`~~~~ ~~~~`/test/`~~~~ 等测试目录。~~

#### ~~H~~unk 解析

#### hunk划分精度

`--granularity` 参数控制 fix\_patch 的划分精度：

|粒度|划分方式|hunk 数|行号精度|迭代次数|适用场景|
|---|---|---|---|---|---|
|`coarse`|原样保留 fix\_patch 的 `@@` hunk|最少|\~150 行/hunk|最少|快速扫描|
|`medium`|按连续 `+`/`-` 块分割大 hunk 如何分割？最长连续修改|居中|\~10 行/hunk|居中|**默认推荐**|
|`fine`|每个连续变更块独立|最多|\~3 行/hunk|最多|精确定位|



**~~粒度越细，行号越精确，但仿真次数越多。~~**~~ 每次仿真包括 Verilator 编译（子模块 \~30s，全芯片 \~2min）\+ 仿真运行（\~1s）。~~

~~将 unified diff 格式解析为结构化 Hunk 对象：~~

```Python
~~class Hunk:~~
~~    file: str              # 修改的文件路径~~
~~    ~~~~header: str            # @@ -old,count +new,count @@~~
~~    lines: List[str]       # diff 行（含 + / - / 空格前缀）~~
~~    line_start_old: int    # buggy 代码起始行号~~
~~    line_count_old: int    # ~~~~buggy 代码行数~~
~~    line_start_new: int    # fixed 代码起始行号~~
~~    line_count_new: int    # ~~~~fixed 代码行数~~
~~    ~~
~~    ~~~~def init_from_string(self, diff):~~
~~    从diff hunk字符串中实例化Hunk类~~
~~    ~~
~~    def to_diff_string(self):~~
~~    Hunk类转换为unfied diff格式~~
```

---

### ~~仿真驱动 Hunk 二分~~

```Plain Text
~~前提:~~
~~全部 hunk 一起 apply → test PASS ✅~~
~~不 apply 任何 hunk   → test FAIL ❌~~

~~二分排除:~~
~~  排除一组 hunk → 跑仿真~~
~~    ├── 仍 PASS → 被排除的 hunk 不是根因（可删）~~
~~    └── 变 FAIL → 被排除的 hunk 包含根因（必须保留）~~

~~递归缩小候选集 → 收敛到最小根因 hunk 集~~
```



#### ~~仿真环境~~



~~使用本地 Verilator \+ ibex 源码：~~



```Plain Text
~~ibex 源码 (一份, 覆盖全部 PR)~~
~~  │~~
~~  ├── git checkout <base_commit>     # 切换到 buggy 版本~~
~~  ├── git apply <partial_patch>      # 应用部分 hunk~~
~~  ├── verilator 编译 + 仿真           # 运行 tb_script~~
~~  └── 解析 HWE_BENCH_RESULTS         # PASS / FAIL~~
```



#### ~~示例：PR\-166（decoder 非法指令检测，2 hunks）~~



```Plain Text
~~H1: @@ -253,19 +253,20 @@  分支条件重构~~
~~H2: @@ -422,7  +423,7  @@  非法指令检测逻辑修改~~

~~Test 1: full_patch  → PASS ✅~~
~~Test 2: empty       → FAIL ✅~~
~~Test 3: keep=[H2]   → PASS ✅  → H1 是 nonessential（分支条件重构，不影响功能）~~
~~Test 4: exclude H1  → 自动确认 H2 是 essential~~
~~Test 5: exclude H2  → FAIL ❌  → H2 确实是 essential（非法指令检测逻辑）~~

~~结果: essential=[H2], nonessential=[H1], 5 iterations~~
```



#### ~~二分排除算法~~



```Python
~~def method_b_bisect(hunks):~~
~~    # Phase 0: 验证 baseline~~
~~    assert run_test(full_patch) == 'PASS'~~
~~    assert run_test(empty_patch) == 'FAIL'~~
~~    ~~
~~    essential = set()~~
~~    nonessential = set()~~
~~    candidates = list(range(len(hunks)))~~
~~    ~~
~~    # Phase 1: 二分排除~~
~~    while len(candidates) > 3:~~
~~        mid = len(candidates) // 2~~
~~        exclude = candidates[:mid]~~
~~        keep = candidates[mid:]~~
~~        ~~
~~        # 测试「排除 exclude 后是否仍 PASS」~~
~~        result = run_test(build_patch(keep))~~
~~        ~~
~~        if result == 'PASS':~~
~~            nonessential.update(exclude)  # 被排除的无关~~
~~            candidates = keep~~
~~        else:~~
~~            candidates = exclude          # 包含根因~~
~~    ~~
~~    # Phase 2: 单个排除验证~~
~~    for idx in candidates:~~
~~        others = [i for i in range(n) if i != idx]~~
~~        result = run_test(build_patch(others))~~
~~        ~~
~~        if result == 'PASS':~~
~~            ~~~~nonessential.add(idx)~~
~~        else:~~
~~            essential.add(idx)~~
~~    ~~
~~    return essential, nonessential~~
```



#### *~~所需工具~~*

---

### ~~源码~~

~~https://github\.com/sishuyan/root\-cause~~

~~注：以上方法出现Bug，或者运行时间过长/超时，则采用人手工标注的方法；上述方法只是确认Bug大致范围，缩小范围后最后都人工确认下，目前只有58个样本，人工工作量不大~~



## 输出：bug\_type、bug\_desc 与 fix\_hint

在 `bug_lineno` 人工确认后，用一个 Prompt 同时生成 `bug_type`、`bug_desc` 和 `fix_hint`，保证三者与同一个 `bug_lineno` 一一对应。

### 目前分出的 8 个 Bug Type

```JSON
[
  {
    "level1": "Functional Bug",
    "level2": "Logic Operation Bug",
    "definition": "Wrong expression, boolean logic, arithmetic, comparison, bit operation, signedness, or width handling."
  },
  {
    "level1": "Functional Bug",
    "level2": "State Machine Bug",
    "definition": "Wrong FSM transition, hold, exit, initialization, recovery, or sequencing."
  },
  {
    "level1": "Functional Bug",
    "level2": "Control Flow Bug",
    "definition": "Wrong valid/ready, enable, request/response, arbitration, stall, flush, or control propagation."
  },
  {
    "level1": "Functional Bug",
    "level2": "Data Flow Bug",
    "definition": "Wrong data selection, forwarding, buffering, memory access, address/index use, or value propagation."
  },
  {
    "level1": "Functional Bug",
    "level2": "Boundary Condition Bug",
    "definition": "Wrong off-by-one, range limit, reserved value, encoding boundary, width limit, or corner case."
  },
  {
    "level1": "Functional Bug",
    "level2": "Exception Scenario Bug",
    "definition": "Wrong exception, interrupt, trap, fault, privilege, permission, CSR, or security access behavior."
  },
  {
    "level1": "Non-functional Bug",
    "level2": "Configuration Bug",
    "definition": "Wrong parameter, macro, feature switch, generate/elaboration choice, or configuration combination."
  },
  {
    "level1": "Non-functional Bug",
    "level2": "Syntax Error",
    "definition": "Verilog/SystemVerilog syntax or compile-time form error."
  }
]
```

### 生成 Prompt

```Plain Text
You are a senior RTL benchmark bug annotation assistant.

For each confirmed root-cause hunk, generate:
1. Bug Type
2. Bug Description
3. Fix Hint

Use the spec, confirmed root-cause hunk, RC code block, buggy RTL code, and PR evidence. Classify by semantic evidence rather than keywords alone.

## Repo Information
Repository: {repo}
Language: {lang}

## Spec
{spec_text}

## Confirmed Root-Cause Hunks and Code Blocks
The following RC annotations provide hunk_id and the corresponding root-cause code block for each confirmed hunk.

Expected format:
[
  {
    "hunk_id": "HUNK_1",
    "root_cause_code": "..."
  },
  {
    "hunk_id": "HUNK_2",
    "root_cause_code": "..."
  }
]

{root_cause_hunks}

## Buggy RTL Code Before Merge
The following contains the full pre-merge source code of all files that contain diff hunks in this PR.
<BEGIN of CODE>
{rtl_code}
<END of CODE>

## Evidence
PR Title: {pr_title}
PR Body:
{pr_body}

Issue:
{issue_text}

Commit Message:
{commit_message}

## Allowed Bug Types
- Functional Bug / Logic Operation Bug: wrong expression, boolean logic, arithmetic, comparison, bit operation, signedness, or width handling.
- Functional Bug / State Machine Bug: wrong FSM transition, hold, exit, initialization, recovery, or sequencing.
- Functional Bug / Control Flow Bug: wrong valid/ready, enable, request/response, arbitration, stall, flush, or control propagation.
- Functional Bug / Data Flow Bug: wrong data selection, forwarding, buffering, memory access, address/index use, or value propagation.
- Functional Bug / Boundary Condition Bug: wrong off-by-one, range limit, reserved value, encoding boundary, width limit, or corner case.
- Functional Bug / Exception Scenario Bug: wrong exception, interrupt, trap, fault, privilege, permission, CSR, or security access behavior.
- Non-functional Bug / Configuration Bug: wrong parameter, macro, feature switch, generate/elaboration choice, or configuration combination.
- Non-functional Bug / Syntax Error: Verilog/SystemVerilog syntax or compile-time form error.

## Output
Output valid JSON only. Follow the same `bug_info` format as the output section. Replace placeholders with the generated values.

{
  "bug_info": [[
    {
      "hunk_id": "HUNK_1",
      "bug_type": {
        "level1": "...",
        "level2": "..."
      },
      "bug_desc": string,
      "fix_hint": string
    },
    {
      "hunk_id": "HUNK_2",
      "bug_type": {
        "level1": "...",
        "level2": "..."
      },
      "bug_desc": string,
      "fix_hint": string
    }
  ]]
}

## Rules
1. Each block in bug_info corresponds to one confirmed root-cause hunk.
2. Copy hunk_id from the provided RC hunk annotation.
3. If there are multiple confirmed hunks in the same file, output one block for each hunk in the same inner list.
4. Use only the allowed bug types and exact spelling.
5. bug_desc should describe the trigger condition, observable failure, and root-cause scope when supported by evidence.
6. fix_hint should describe the repair direction at a high level and may mention relevant modules, files, or signals from the evidence.
7. Do not copy patch code or invent unsupported tests, logs, or behavior.
```

分类出的Bug再进行正则表达式匹配确认

```Python
import re

BUG_TYPE_PATTERN = re.compile(
    r"^(Functional Bug::("
    r"Logic Operation Bug|"
    r"State Machine Bug|"
    r"Control Flow Bug|"
    r"Data Flow Bug|"
    r"Boundary Condition Bug|"
    r"Exception Scenario Bug"
    r")|"
    r"Non-functional Bug::("
    r"Configuration Bug|"
    r"Syntax Error"
    r"))$"
)

def validate_bug_type_regex(bug_type: dict) -> bool:
    if not isinstance(bug_type, dict):
        return False

    level1 = bug_type.get("level1")
    level2 = bug_type.get("level2")

    if not isinstance(level1, str) or not isinstance(level2, str):
        return False

    return bool(BUG_TYPE_PATTERN.fullmatch(f"{level1}::{level2}"))
```

# Metrics

加权评分

|指标|权重|含义|计算方法|
|---|---|---|---|
|行号精确度 \(LP\)<br>|20%|报告的行号范围是否指向真实 bug|匹配数 除以 LLM报告总数。等价于 bug 级精确度|
|文件准确度 \(FA\)|5%|报告的文件名是否正确|文件命中的报告数 除以 LLM报告总数。命中 = 报告文件属于该任务 BugInfo 源文件集合|
|召回率 \(R\)|20%|BugInfo 中多少真实 bug 被发现|匹配数 除以 BugInfo bug 总数。BugInfo bug 总数为 0 的 no\-bug 任务不计入聚合|
|F1|10%|精确度与召回率的综合平衡|2 × 精确度 × 召回率 除以 \(精确度 \+ 召回率\)，其中精确度 = 匹配数/LLM报告总数|



## 需考虑的情况：

1. PR中对应的代码还有一些Bug没发现，在之后的PR中被发现（或者不被发现） \-\> 仅关注Recall，弱化Precision？

2. 





# Docker环境









# 相关资源

1. Spec参考

    https://github\.com/hkust\-zhiyao/SpecLLM/tree/main

