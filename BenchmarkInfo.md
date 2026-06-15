# BenchmarkInfo

输入\&输出格式

> **以josnl文件组织，每一行是一个json （参考下HWE\-bench格式）**
> 



```JSON
{
    input: {},
    bug_info: [],
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
"bug_info": [[
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
]]
```



示例：@李林轩

```JSON
"bug_info": [[
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
]]
```

# 构建流程（Repo PR采集\&筛选）@林子涵

- **S1 数据采集与粗筛**：从目标仓库（如 lowRISC/ibex）通过 GitHub API 拉取已合并的 bug 修复 PR，并过滤为“仅修改单个 RTL 文件”的候选集。

    S1 复用 hwe-bench 的采集逻辑，拆为 6 个子步骤，中间产物以 JSONL 持久化，支持断点续跑：

    1. **S1.1 拉取所有 PR**：通过 GitHub API 拉取目标仓库的全部 PR，并为每个 PR 拉取其 commit 列表。输出 `s01_01_prs.jsonl`。
    2. **S1.2 按 issue 过滤**：用 LLM 从 PR title、PR body 以及所有 commit messages 中提取关联 issue 号；仅保留已合并（closed && merged_at 非空）且成功解析出 issue 号的 PR。输出 `s01_02_issue_linked_prs.jsonl`。
    3. **S1.3 拉取 issue 详情**：根据 S1.2 得到的 issue 号列表，拉取每个 issue 的 title 和 body。输出 `s01_03_issues.jsonl`。
    4. **S1.4 合并 PR 与 issue 数据**：将 S1.2 的 PR 记录与 S1.3 的 issue 记录按 issue 号合并。输出 `s01_04_merged_prs.jsonl`。
    5. **S1.5 抽取 patch**：通过 GitHub compare API 获取 base commit 到 head commit 的完整 unified diff，并按文件路径拆分为 `fix_patch`（非测试文件）和 `test_patch`（测试相关文件）。输出 `s01_05_patches.jsonl`。
    6. **S1.6 patch 粗筛与单 RTL 文件过滤**：仅保留 `fix_patch` 中恰好修改了 1 个 RTL 文件（`.v` / `.sv` / `.svh`）的 PR；单个文件修改长度不限；丢弃测试目录、markdown、多 RTL 文件修改等。输出 `s01_06_single_rtl_candidates.jsonl`。

    脚本位置：`src/s1_collection/`，包含 `s1_01_fetch_prs.py` ~ `s1_06_filter_patches.py` 及 `s1_pipeline.py` orchestrator。

    环境依赖：`PyGithub`、`unidiff`、`requests`、`tqdm`、`openai`；密钥 `GITHUB_TOKEN` 与 `DEEPSEEK_API_KEY`。

    S1 输出字段说明：
    - 进入最终 `input` 包的字段：`repo`、`pr_id`、`lang`、`rtl_files`、`commit_id`（base commit SHA）、`timestamp`（`merged_at`）。
    - 供 S2/S3 使用的辅助证据字段：`pr_title`、`pr_body`、`issue_title`、`issue_body`、`commit_message`、`fix_patch`、`test_patch`、`modified_files`、`lines_added`/`lines_removed`。
    - S1 内部字段：`commits` 列表（仅用于 S1.2 issue 提取）。

- **S****2**** 输入包构造**

    - repo，pr\_id，lang（按文件后缀名）, rtl\_files，commit\_id，timestamp：这些可以根据PR信息直接确定，属于meta data

    - **spec：需要LLM来生成，后面章节会讲具体方法。**

- **S****3**** 输出包构造****\(****bug\_lineno、bug\_type、****bug\_desc、fix\_hint ****\)**：

    - **bug\_lineno：通过确定性算法（非LLM\-based）可以确定，后面章节会讲具体方法。**

    - **bug\_type、bug\_desc、fix\_hint：需要LLM来生成，后面章节会讲具体方法。**

# 输入字段构建@林子涵

## Spec 生成

**Spec 是模块功能规格描述，作为 LLM 审查代码的基准**** ****——**** ****LLM 需对照 spec 检查 RTL 代码行为是否一致。**

spec 描述**正确行为应该是什么**（WHAT），不描述实现方式（HOW），不泄露 bug 位置。

|生成原则|说明|
|---|---|
|行为级描述|描述可观测的正确行为，不描述内部实现|
|自包含|不熟悉该项目的工程师阅读后应能理解模块应该做什么|
|防泄露|不提及信号名、行号、代码结构、修复方法、测试方法|

### 策略

**反推/生成**** \+ 审查**（两步走）：

1. **Generate**：LLM 读取修复后的完整的RTL文件 \+ 官方文档的spec，LLM生成spec行为规格

    1. 修复后的完整的RTL文件

    2. 官方文档的spec

        1. 例如：https://github\.com/chipsalliance/caliptra\-rtl/blob/main/docs/CaliptraIntegrationSpecification\.md、https://github\.com/lowRISC/ibex/blob/master/doc/01\_overview/compliance\.rst

        2. 目前我们关心的三个repo有官方文档，可以通过**repo\-specific的方法**去提取和**当前RTL文件**最相关的文档部分@林子涵如何提取，构建一个标准的RAG？query如何设计（fix之后的代码\+PR title\&body\+patch）

        3. **如果没有官方文档，在prompt层面上这一部分为空即可**

2. **Review**：第二个 LLM 检查 spec 是否泄露实现细节（信号名、行号、fix 方法、patch 片段），通过则采纳，否则**人工修改spec描述（如果人工也不太好改，直接丢掉）**。

### Spec 格式

"spec"字段为纯 Markdown 文本，结构如下： @林子涵建议不过于限制格式，这个不是关键创新点，设计过多（且不确定是否合理）容易引发质疑，直接LLM按照一定原则生成free\-format的就行

```Markdown
# <module_name>

## Overview
<1-3 sentences describing module purpose and system role>

## Functional Behavior
- <behavioral assertion 1: concrete and judgeable>
- <behavioral assertion 2>
- ...

## Interface Constraints (optional)
- <protocol requirements or interface-level constraints>
```

### 生成流程（伪代码）

```Python
def generate_spec(input: dict) -> dict:
    """
    主入口：读取原始材料，调用 LLM 生成 spec，审查后返回。
    代码层无分支，由 LLM 根据材料自行选择 Tier 策略。
    """
    materials = load_materials(input)

    prompt = build_generation_prompt(input, materials)
    spec_text = call_llm(prompt, temperature=0)

    if not review_spec(spec_text, materials["patch"]):
        spec_text = fix_with_feedback(spec_text, materials)

    return {**input, "spec": spec_text}
```

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

### Official Specification Snippets
{Official Spec}

## Output Format
Generate a Markdown spec with this exact structure:

```
# <module_name>
## Overview
[1-3 sentences describing module purpose and system role]

## Functional Behavior
- [behavioral assertion using must/shall/should]
- ...

## Interface Constraints (optional)
[protocol requirements]
```

## Rules
1. Describe WHAT (correct behavior), not HOW (implementation).
2. Do NOT mention signal names, line numbers, code structure, or fix methods.
3. Use normative language: must, shall, should.
4. Self-contained: an engineer unfamiliar with the project should understand what the module should do.
5. Do NOT use: incorrectly, broken, fixed, bug, patched, repaired.
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

### Official Specification Snippets
{Official Spec}

### Generated Specification
```markdown
{generated spec}
```

## Checks (true / false)
1. describes_correct_behavior: Does the spec use must/shall/should to describe correct behavior? No "bug/fixed/broken/incorrectly".
2. no_patch_leakage: Does the **generated specification** contain NO signal names, variable names, assign expressions, or line numbers from the patch?
3. sufficient_for_diagnosis: Does the generated specification contain at least one behavioral assertion directly related to the functional area modified by the patch?

## Output
Output valid JSON only, no markdown fences:
{"passed": true, "checks": {"describes_correct_behavior": true, "no_patch_leakage": true, "sufficient_for_diagnosis": true}, "reasoning": "one sentence summary"}
```

# 输出字段构建@李林轩

## bug\_lineno 

LLM辅助过滤后再经过人工审查resaon，LLM的prompt如下，产出用于 root\-cause 人工审查标注的候选 hunk shortlist：

```Plain Text
You are a senior RTL root-cause hunk filtering assistant.

Filter the fix patch hunks and keep the hunks that may contain the root cause.
This step produces a hunk shortlist for human root-cause review and annotation.

## Case
Repository: {repo}
PR ID: {pr_id}
Language: {lang}
Base Commit Before Fix: {commit_id}

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

{rtl_code}

## Fix Patch Hunks
The input fix_patch is already split into hunks with hunk_id.

Expected format:
[
  "HUNK_1",
  "HUNK_2"
]

{fix_patch}

## Output
Output valid JSON only. Use hunk_id values from Fix Patch Hunks.

{
  "candidate_root_cause_hunks": [
    {
      "file_path": "...",
      "hunk_ids": [
        "HUNK_1",
        "HUNK_2"
      ],
      "reason": "Explain why these hunks may contain the root cause."
    }
  ],
  "filtered_non_root_cause_hunks": [
    {
      "file_path": "...",
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
4. Every output item must include file_path so human annotators know which file the hunk ids belong to.
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

#### ~~Hunk 解析~~

#### ~~hunk划分精度~~

~~`--granularity`~~~~ 参数控制 fix\_patch 的划分精度：~~

|~~粒度~~|~~划分方式~~|~~hunk 数~~|~~行号精度~~|~~迭代次数~~|~~适用场景~~|
|---|---|---|---|---|---|
|~~`coarse`~~|~~原样保留 fix\_patch 的 ~~~~`@@`~~~~ hunk~~|~~最少~~|~~\~150 行/hunk~~|~~最少~~|~~快速扫描~~|
|~~`medium`~~|~~按连续 ~~~~`+`~~~~/~~~~`-`~~~~ 块分割大 hunk~~~~ ~~~~如何分割？最长连续修改~~|~~居中~~|~~\~10 行/hunk~~|~~居中~~|**~~默认推荐~~**|
|~~`fine`~~|~~每个连续变更块独立~~|~~最多~~|~~\~3 行/hunk~~|~~最多~~|~~精确定位~~|



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
The following RC annotations provide hunk_id, file_path, and the corresponding root-cause code block for each confirmed hunk.

Expected format:
[
  {
    "hunk_id": "HUNK_1",
    "file_path": "...",
    "root_cause_code": "..."
  },
  {
    "hunk_id": "HUNK_2",
    "file_path": "...",
    "root_cause_code": "..."
  }
]

Root Cause Code Hunks
{root_cause_hunks}

## Buggy RTL Code Before Merge
The following contains the full pre-merge source code of all files that contain diff hunks in this PR.

{rtl_code}

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
      "file_path": "...",
      "bug_type": {
        "level1": "...",
        "level2": "..."
      },
      "bug_desc": string,
      "fix_hint": string
    },
    {
      "hunk_id": "HUNK_2",
      "file_path": "...",
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
2. Copy hunk_id and file_path from the provided RC hunk annotation.
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

2. XXXX

|~~负责人~~|~~Repo~~|~~Link~~|~~简介~~|
|---|---|---|---|
|~~书琰~~|~~ibex~~|~~https://github\.com/lowRISC/ibex~~|~~小型、可参数化的 32\-bit RISC\-V CPU core，主要面向嵌入式控制场景。~~|
||~~CVA6~~|~~https://github\.com/openhwgroup/cva6~~|~~可配置的 6 级流水 RISC\-V core，应用级配置可以运行 Linux。~~|
|~~林轩~~|~~XiangShan~~|~~https://github\.com/OpenXiangShan/XiangShan~~|~~开源高性能 RISC\-V 处理器项目，复杂度和微架构规模更接近高性能 CPU。~~|
||~~Rocket Chip~~|~~https://github\.com/chipsalliance/rocket\-chip~~|~~基于 Chisel/Scala 的 RISC\-V SoC 生成器，可生成 Rocket Core 及其 SoC 外围结构。~~|
|~~子涵~~|~~Caliptra RTL~~|~~https://github\.com/chipsalliance/caliptra\-rtl~~|~~Caliptra Root of Trust IP 的 RTL 硬件设计仓库，偏安全根信任模块。~~|
||~~opentitan~~|~~https://github\.com/lowRISC/opentitan~~|~~开源 silicon Root of Trust 项目，包含硬件、软件和安全 IP 的 monorepo。~~|



|负责人|Repo|Link|简介|
|---|---|---|---|
|书琰|ibex|||
|||||
|林轩|CVA6|||
|||||
|子涵|Caliptra RTL|||
|||||



