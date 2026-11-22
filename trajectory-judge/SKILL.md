---
name: trajectory-judge
description: >-
  Evaluates historical conversation and subagent trajectories where a skill was
  used and executes a 5-step subagent-driven improvement pipeline: (1) distributed
  trajectory inspection into markdown findings files, (2) raw recommendation
  extraction into recommendations_raw.md, (3) deduplication and component-based
  consolidation into recommendations_consolidated.md, (4) independent technical
  scrutiny for regressions and side effects into recommendations_final.md, and
  (5) component-isolated implementation followed by independent verification.
  Use whenever asked to judge, audit, or improve a skill based on past trajectories.
---

# Trajectory Judge

Use this 5-step, subagent- orchestrated procedure to systematically audit historical trajectories (both parent orchestrator conversations and child subagent conversations) where a target skill was used, extract and consolidate evidence-backed recommendations, subject those recommendations to independent technical scrutiny for side effects, apply approved improvements with strict file isolation, and independently verify the updated skill and tools.

---

## Architecture: Zero-Compression 5-Step Subagent Pipeline

Inspecting multi-megabyte JSONL transcripts, reading 10–20 markdown findings reports (250–450 KB total), consolidating 100+ KB of recommendations, auditing multi-thousand-line Python helper scripts, and verifying code changes in a single main agent conversation causes **catastrophic context bloat (>120k–200k tokens) and repeated context compression drops**.

To keep the Main Agent's context window lightweight across the entire lifecycle, `trajectory-judge` enforces a **5-Step Distributed Subagent Pipeline** where heavy reading, writing, synthesis, scrutiny, editing, and testing are offloaded to specialized subagents that persist intermediate artifacts on disk in `<findings-dir>/` (e.g., `<workspace>/findings-YYYY-MM-DD/`):

```
=====================================================================================
STEP 1: Trajectory Discovery & Distributed Subagent Inspection
=====================================================================================
Main Agent runs `scan_trajectories.py discover` -> spawns Trajectory Inspector Subagents
  ├── Inspector Subagent 1 (Outlier/Error Trajectory) ──> <findings-dir>/<conv-id-1>.md
  ├── Inspector Subagent 2 (Parent Orchestrator)      ──> <findings-dir>/parent_<id>.md
  └── Inspector Subagent N (Clean Batch Runs)         ──> <findings-dir>/batch_<name>.md
  (Optional: Main Agent queries Inspector Subagents via `send_message` for cross-run checks)

=====================================================================================
STEP 2: Distributed Recommendation Extraction (`recommendations_raw.md`)
=====================================================================================
Main Agent spawns 1 Recommendation Extractor Subagent per findings `.md` file
  ├── Extractor Subagent 1 reads `<conv-id-1>.md` ──> <findings-dir>/raw_extracted/01_<stem>.md
  └── Extractor Subagent N reads `batch_<name>.md` ──> <findings-dir>/raw_extracted/N_<stem>.md
Main Agent runs `scan_trajectories.py assemble-raw` ──> <findings-dir>/recommendations_raw.md

=====================================================================================
STEP 3: Deduplication & Logical Consolidation (`recommendations_consolidated.md`)
=====================================================================================
Main Agent spawns Recommendation Consolidator Subagent
  └── Reads `recommendations_raw.md`, deduplicates & groups by Target Component / Theme
      while preserving 100% of actionable specs ──> <findings-dir>/recommendations_consolidated.md

=====================================================================================
STEP 4: Independent Scrutiny & Side-Effect Audit (`recommendations_final.md`)
=====================================================================================
Main Agent spawns Independent Scrutiny Auditor Subagent (Blind to raw/trajectory files)
  ├── Audits `recommendations_consolidated.md` ONLY against live `SKILL.md` & `scripts/`
  ├── Flags 🔴 High-Risk Regressions, 🟡 Modifications Needed, 🟢 Safe As-Is
  ├── Main Agent presents scrutiny verdicts to User for confirmation (User Decision Gate)
  └── Subagent incorporates approved adjustments ──> <findings-dir>/recommendations_final.md

=====================================================================================
STEP 5: Component-Isolated Implementation & Independent Verification
=====================================================================================
Step 5A (Implementation): Main Agent spawns Component Engineer Subagents (Disjoint File Ownership)
  ├── Engineer Subagent A (ONLY edits `SKILL.md`)
  ├── Engineer Subagent B (ONLY edits `scripts/tool_a.py`)
  └── Engineer Subagent C (ONLY edits companion skill / `scripts/tool_b.py`)
Step 5B (Independent Verification): Main Agent spawns NEW Verification Auditor Subagents
  ├── Auditor Subagent 1 (Line-by-line & live CLI audit of `SKILL.md` vs `recommendations_final.md`)
  ├── Auditor Subagent 2 (Line-by-line & live CLI audit of `scripts/tool_a.py`)
  └── Auditor Subagent 3 (Line-by-line & live CLI audit of companion skill / `scripts/tool_b.py`)
```

### Core Orchestration Rules for the Main Agent
1. **Never Read Bulk Artifacts in the Main Agent**: The Main Agent must **not** read all individual `<findings-dir>/*.md` files, must **not** read or write the full `recommendations_raw.md` or `recommendations_consolidated.md` in its own context, and must **not** read multi-thousand-line target scripts directly. Delegate those reads/writes to the step-specific subagents, which return compact summaries to the Main Agent.
2. **Use `TypeName: "self"` for File-Writing Subagents**: In Antigravity, `research` subagents run in a read-only sandbox and cannot call `write_file`, `edit_file`, or `run_command` with `BypassSandbox: true`. Always invoke subagents that write markdown files or run CLI tools with `TypeName: "self"`.
3. **Pass `BypassSandbox: true` When Scanning `<appDataDir>/brain/` on Windows**: The Windows terminal sandbox restricts directory listing and file reads inside `<appDataDir>/brain/` to the current conversation only (`PermissionError: [WinError 5] Access is denied` on all other conversations). Always run `scan_trajectories.py discover` and `scan_trajectories.py inspect` with `BypassSandbox: true`.
4. **Zero-Polling Reactive Wakeup**: After spawning subagents via `invoke_subagent` or messaging them via `send_message`, do **not** poll `manage_subagents` or `manage_task` in a loop. Stop calling tools and let the system automatically wake you when subagents complete.
5. **Flexible Entry & Pause Points**:
   - If the user asks to run the full end-to-end procedure, execute Steps 1 $\rightarrow$ 4 (pausing at the Step 4 User Decision Gate so the user can review the scrutiny verdicts before applying edits in Step 5, unless the user explicitly requested autonomous application).
   - If the user points to an existing `<findings-dir>/` (e.g., `findings-2026-11-21/recommendations_consolidated.md`), start directly at the corresponding step.

---

## Helper Scripts (`scripts/scan_trajectories.py`)

Always run `discover` and `inspect` with `BypassSandbox: true` on Windows so historical conversations in `<appDataDir>/brain/` are accessible:

- **Discover trajectories for a skill** (maps parent $\leftrightarrow$ subagent hierarchies, skill edit milestones, context token curves, compression drops, tool counts, errors, inline `python -c` calls, repeated file reads/edits, and substantive user turns):
  ```powershell
  # Discover all trajectories created/active after the latest skill update:
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" discover --skill <skill-name> --executed-only --after-last-update

  # Or filter by an explicit ISO timestamp cutoff:
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" discover --skill <skill-name> --executed-only --since 2026-11-06T06:14:21Z
  ```
  *(Omit `--executed-only` if you also want to list `SKILL_AUTHORING_OR_META` trajectories where the skill was edited or judged.)*

- **Inspect diagnostic summary of a single trajectory**:
  ```powershell
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --summary
  ```

- **Inspect specific steps, thinking traces, or errors** (reads `transcript_full.jsonl` when `--full` is passed to bypass `truncated_fields` without loading the whole file):
  ```powershell
  # Inspect only steps with failed commands or non-zero exit codes:
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --errors-only --full

  # Inspect specific step ranges (including model thinking traces and tool calls):
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --steps 10-25 --full

  # Inspect only USER_INPUT and PLANNER_RESPONSE steps:
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --planner-only
  ```

- **Assemble extracted raw recommendations into `recommendations_raw.md`** (used in Step 2 so the Main Agent never loads or writes 100+ KB of raw recommendations):
  ```powershell
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" assemble-raw --input-dir "<findings-dir>/raw_extracted" --out-file "<findings-dir>/recommendations_raw.md" --skill <skill-name> --cleanup
  ```

---

## The 5-Step Procedure

### Step 1: Trajectory Discovery & Distributed Subagent Inspection

#### 1.1 Discovery & Sharding Strategy (Main Agent)
1. **Determine Findings Directory**: Use `<workspace>/findings-<YYYY-MM-DD>/` (e.g., `c:\Users\Tilman\Antigravity\SkillUpdates\findings-2026-11-22\`) or the directory specified by the user.
2. **Run Trajectory Discovery**: Execute `scan_trajectories.py discover --skill <skill-name> --executed-only --after-last-update` (with `BypassSandbox: true`). If the user specified a custom date range or all trajectories, adjust `--since` accordingly.
3. **Partition Trajectories Across Inspector Subagents**:
   - **High-Signal Outliers & Error Runs**: Assign 1 dedicated Trajectory Inspector subagent per trajectory (or pair of closely related trajectories) that has substantive user corrections, command errors, context compression drops, high step counts, or repeated file reads/edits.
   - **Parent Orchestrator Trajectory**: If the target skill was invoked by a parent orchestrator (e.g., `hiking-blog-post` spawning `photo-analysis` subagents), assign 1 dedicated Trajectory Inspector subagent to audit the post-update step range of the parent orchestrator and cross-skill handoffs.
   - **Clean / Standard Batch Runs**: Group clean, low-step trajectories into logical batches (e.g., 10–20 clean trajectories per batch inspector subagent) so subtle systemic inefficiencies (such as universal 2-part `SKILL.md` reads from exceeding the `46,080-byte` `read_file` limit, unbatched tool calls, or minor formatting lapses) are audited without spawning dozens of redundant subagents.
4. **Spawn Inspector Subagents**: Invoke each subagent using `invoke_subagent` (`TypeName: "self"`) with the prompt template below. Track `target_id_or_batch -> subagent_conversation_id -> findings_file_path`.

#### 1.2 Step 1 Subagent Prompt Template (Trajectory Inspector)
```markdown
You are a dedicated Trajectory Inspector auditing historical Antigravity trajectory(s) where the skill `<skill-name>` was used.

### Target Trajectory Metadata:
- **Conversation ID(s)**: <target-conversation-id-or-list>
- **Parent Conversation ID**: <parent-id-or-None> (If auditing a parent segment, focus on steps <start-end>)
- **Timestamps**: <created_at> to <ended_at>
- **Target Skill**: `<skill-name>` (`<path-to-skill-SKILL.md>`) and its `scripts/` directory
- **Companion Skill(s)** (if applicable): `<companion-skill-path>`
- **Findings Output File**: `<findings-dir>/<output-filename>.md`

### Key Diagnostic Signals from Discovery:
- Steps & Context Tokens: <steps> | <first-tokens> -> <peak-tokens>
- Flagged Errors / Exit Codes: <error-step-list>
- Inline `python -c` / Polling Calls: <inline-python-and-polling-list>
- Context Compression Drops: <compression-events-list>
- Substantive User Turns / Corrections: <user-turn-step-list>
- Top Repeated File Reads / Edits: <repeated-reads-edits-summary>

### Your Inspection Mission:
1. Read `C:\Users\Tilman\.gemini\config\skills\trajectory-judge\SKILL.md` (specifically the 7 Core Evaluation Dimensions and Standard Findings Template), the target skill's `SKILL.md`, any relevant helper scripts in `scripts/`, and companion skill `SKILL.md` files.
2. Run `scan_trajectories.py inspect` (ALWAYS pass `BypassSandbox: true` on Windows so `<appDataDir>/brain/` is readable):
   - Summary: `python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --summary`
   - Errors & critical step ranges: `python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --steps <step-range> --full`
3. Examine the `thinking` trace of `PLANNER_RESPONSE` steps around every user correction, error, token spike, compression boundary, and key decision to uncover confirmation bias, motivated reasoning, or instruction drift.
4. Check `SKILL.md` byte size and `read_file` step behavior (e.g., whether `SKILL.md` exceeds `46,080 bytes` and forces 2-part `ByteOffset` reads, or whether tool JSON outputs exceed `~8–10 KB` and trigger mid-output truncation).
5. Write your comprehensive markdown report to `<findings-dir>/<output-filename>.md` following the Standard Individual/Batch Trajectory Findings Template in `trajectory-judge/SKILL.md`. Ensure `## 6. Recommended Skill & Tool Fixes (Local Perspective)` contains complete, actionable specifications (exact file paths, line numbers, function names, CLI flags, and proposed code/wording changes).
6. Reply to the main agent with ONLY a concise 1–2 paragraph summary of your findings and confirmation of the written file path. Do NOT paste the full markdown report into your reply.
```

#### 1.3 Standard Individual/Batch Trajectory Findings Template
Each Step 1 Inspector Subagent must write its `<findings-dir>/<output-filename>.md` using this structure:
```markdown
# Trajectory Inspection Report: `<conversation-id-or-batch-name>`

- **Target Skill**: `<skill-name>`
- **Conversation Link(s)**: [<short-id>](conversation://<conversation-id>)
- **Parent Conversation**: [<short-parent-id>](conversation://<parent-id>) *(if applicable)*
- **Created / Ended**: <ISO timestamps>
- **Steps**: <total-steps> | **Peak Context**: <peak-tokens> tokens | **Compression Drops**: <count>
- **Error Steps**: <list> | **Substantive User Turns**: <count>

## 1. Executive Summary & Trajectory Arc
[Concise narrative of what the agent was asked to do, where it stumbled, and whether it succeeded, satisficed, or required user intervention.]

## 2. Context Token Curve & Compression Events
[Initial vs. peak tokens, largest single-step token spikes (e.g., reading raw JSONs or 2-part SKILL.md reads), compression drops, and post-compression amnesia.]

## 3. Substantive User Interventions & Corrections
[For every user correction: exact quote, what the agent did right before, quote from the model's `thinking` trace showing why it erred, and exact omission/ambiguity in `SKILL.md`.]

## 4. Evaluation Across the 7 Core Dimensions
### Dimension 1: Failed Tool Calls, Syntax Thrashing & Error Recovery
### Dimension 2: Unnecessary, Redundant & Inefficient Tool Calls
### Dimension 3: Confirmation Bias, Anchoring & Motivated Reasoning
### Dimension 4: Goal-Driven Obsession, Reward Hacking & Premature Completion
### Dimension 5: Context Bloat, Compression Amnesia & Instruction Drift
### Dimension 6: Subagent Orchestration & Cross-Skill Handoff Pathologies
### Dimension 7: Skill Description Flaws & Ambiguities

## 5. Key Excerpts (Thinking Traces & Tool Calls)
[Targeted quotes of thinking traces and tool calls/outputs with step numbers.]

## 6. Recommended Skill & Tool Fixes (Local Perspective)
[Numbered, self-contained, actionable recommendations. For EVERY recommendation, include:
- **Title & Target**: Exact file path (`SKILL.md` section/lines, `scripts/<file>.py` function/lines, or companion skill).
- **Problem & Trajectory Evidence**: Exact step numbers, error text, or token impact.
- **Actionable Implementation Specification**: Exact proposed wording, CLI flags, code logic, or guardrails.]
```

#### 1.4 Optional Interactive Cross-Trajectory Follow-Up (`send_message`)
If the Main Agent spots a discrepancy or cross-run hypothesis from the subagents' completion summaries (e.g., whether a pathology seen in Trajectory A also occurred in Trajectory B), it can message the relevant Inspector Subagent via `send_message(Recipient=<subagent_id>, Message=...)` to inspect specific steps, append an `## Addendum: Cross-Trajectory Follow-Up Analysis` section to its findings file, and reply with the answer.

---

### Step 2: Distributed Recommendation Extraction (`recommendations_raw.md`)

Once all Step 1 trajectory analysis files exist in `<findings-dir>/*.md`, extract all recommendations into `<findings-dir>/recommendations_raw.md` **without** the Main Agent reading the findings files or writing the 100+ KB raw file itself.

1. **Spawn Recommendation Extractor Subagents**:
   - For each analysis markdown file `<findings-dir>/<analysis>.md` (excluding `recommendations_*.md`), spawn a dedicated subagent (`TypeName: "self"`, `Role: "Extractor: <stem>"`).
   - Use the **Step 2 Subagent Prompt Template** below so each subagent writes its extracted section to `<findings-dir>/raw_extracted/<stem>.md`.
2. **Deterministic Assembly into `recommendations_raw.md`**:
   - Once all Extractor Subagents complete, run `scan_trajectories.py assemble-raw`:
     ```powershell
     python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" assemble-raw --input-dir "<findings-dir>/raw_extracted" --out-file "<findings-dir>/recommendations_raw.md" --skill <skill-name> --cleanup
     ```
   - This merges all extracted files with a Table of Contents into `<findings-dir>/recommendations_raw.md` and deletes the staging folder `raw_extracted/` at zero context cost to the Main Agent.

#### Step 2 Subagent Prompt Template (Recommendation Extractor)
```markdown
Read the trajectory analysis file at `<findings-dir>/<analysis_file>.md` in full (if the file exceeds 46,080 bytes, use a second `read_file` call with `ByteOffset=46080`).

Your task is to extract ALL recommendations for the `<skill-name>` skill, its helper tools/scripts, and any companion skills/tools discussed in this analysis, and write them to `<findings-dir>/raw_extracted/<analysis_stem>.md`.

### Requirements:
1. Read the entire file so you capture both the dedicated recommendations section (note that the section header may vary across files) AND any specific recommendations, bug fixes, or supporting technical evidence embedded in the rest of the document.
2. Preserve full actionable detail for every recommendation so that it can be used later to make actual code and documentation changes without re-reading the trajectory report:
   - **Original Section Header(s)**
   - **Recommendation Title / Summary**
   - **Target File(s), Function(s), CLI Subcommands, & Line Numbers**
   - **Problem / Empirical Evidence from the Trajectory** (trajectory IDs, step numbers, error messages, token spikes, root causes)
   - **Detailed Actionable Implementation Specification** (exact proposed wording, CLI flags, code logic, regexes, or structural changes)
3. Write the extracted markdown to `<findings-dir>/raw_extracted/<analysis_stem>.md` starting with `# <analysis_file>.md`.
4. Reply to the main agent with ONLY a brief confirmation (the output file path and a 1-line bulleted list of the extracted recommendation titles). Do NOT paste the full extracted text into your reply.
```

---

### Step 3: Deduplication & Logical Consolidation (`recommendations_consolidated.md`)

Delegate deduplication and logical grouping of `<findings-dir>/recommendations_raw.md` to a dedicated **Recommendation Consolidator Subagent** so the Main Agent never loads the 65–150 KB raw file into its context.

1. **Spawn Recommendation Consolidator Subagent**:
   - Invoke a single subagent (`TypeName: "self"`, `Role: "Recommendation Consolidator"`) with the prompt template below.
2. **Receive Compact Outline**:
   - The Consolidator Subagent writes `<findings-dir>/recommendations_consolidated.md` and replies to the Main Agent with only the Group & Item Table of Contents.

#### Step 3 Subagent Prompt Template (Recommendation Consolidator)
```markdown
Read `<findings-dir>/recommendations_raw.md` in full (use multiple `read_file` calls with `ByteOffset` / `StartLine` & `EndLine` if the file exceeds the 46,080-byte per-read limit).

It contains specific recommendations to improve the `<skill-name>` skill, its helper tools/scripts, and companion skills based on prior trajectory analyses.

### Your Consolidation Mission:
1. **Deduplicate**: Merge overlapping or duplicate recommendations that appeared across multiple trajectory reports into unified items. Explicitly list which raw items each consolidated item merges (e.g., `*Consolidates: 1.1, 3.2, 5.1*`) and aggregate all supporting trajectory IDs and step numbers.
2. **Group Logically by Target Component & Architectural Theme**: Organize recommendations into numbered Groups by target file and functional theme (e.g., `Group 1: <skill>/SKILL.md — Size, Structure & Token Hygiene`, `Group 2: <skill>/SKILL.md — Stage 1/2 Workflow & Guardrails`, `Group 3: <skill>/scripts/<script>.py — Core Logic & Caching`, `Group 4: Companion Skill & Tool Alignment`) rather than by trajectory.
3. **Preserve 100% of Actionable Implementation Details**: Do NOT over-summarize or strip technical specifics. Preserve every exact file path, line number, function name, CLI flag, output JSON key, root-cause diagnostic, trajectory evidence citation, and exact proposed code/prompt wording required to implement the changes later.
4. **Write Output**: Save the complete consolidated document (with a linked Table of Contents) to `<findings-dir>/recommendations_consolidated.md`.
5. **Compact Reply**: Reply to the main agent with confirmation that `<findings-dir>/recommendations_consolidated.md` has been written, followed by a concise Table of Contents listing the Groups and recommendation titles.
```

---

### Step 4: Independent Technical Scrutiny, Side-Effect Audit & Finalization (`recommendations_final.md`)

Before applying any recommendation, subject `<findings-dir>/recommendations_consolidated.md` to **independent technical scrutiny** by a fresh subagent that audits the proposed changes directly against the live skill and script codebase.

#### 4.1 Why Independent Scrutiny Must Be Blind to Raw/Trajectory Files
Trajectory inspectors naturally optimize for fixing the local failure they just observed, which frequently leads to **over-fitting, blanket bans that break other valid workflows, cross-skill contract deadlocks, CLI flag collisions, or timeout budget violations**.
- The **Independent Scrutiny Auditor Subagent** MUST be explicitly forbidden from reading `recommendations_raw.md` or any of the individual trajectory analysis files (`<findings-dir>/*.md`).
- Instead, it reads **only** `<findings-dir>/recommendations_consolidated.md` and the **actual live implementation files** (`SKILL.md`, `scripts/*.py`, and companion skills/scripts) to evaluate whether each recommendation makes sense and whether it could negatively impact execution.

#### 4.2 Step 4 Subagent Prompt Template (Independent Scrutiny Auditor)
```markdown
You are an Independent Technical Scrutiny Auditor evaluating proposed updates in `<findings-dir>/recommendations_consolidated.md` for the `<skill-name>` skill and its tools.

### STRICT ISOLATION RULE:
- Do **NOT** read `<findings-dir>/recommendations_raw.md` or any of the original trajectory analysis files in `<findings-dir>/`.
- Base your scrutiny strictly on `<findings-dir>/recommendations_consolidated.md` and the **live codebase** (`<path-to-skill>/SKILL.md`, `<path-to-skill>/scripts/*`, and any referenced companion skills/scripts).

### Your Scrutiny Mission:
1. Read `<findings-dir>/recommendations_consolidated.md` in full (using `ByteOffset` if `> 46,080 bytes`).
2. Read the actual current implementation of the target skill (`SKILL.md`), all target helper scripts in `scripts/`, and any referenced companion skill files.
3. Scrutinize every recommendation for potential negative impacts, side effects, or flaws, checking specifically for:
   - **Cross-Step / Cross-Skill Workflow Deadlocks & Data Starvation**: Does combining two recommendations (e.g., compacting `send_message` while also stripping an output field from a companion script, while forbidding `read_file` on a staging file) starve the parent orchestrator or break a downstream phase?
   - **Overly Rigid Blanket Bans**: Does a proposed rule (e.g., banning separate `--overview` and `--box` crop calls, or forcing a narrow `--types` filter on initial discovery) break legitimate edge cases or poison a shared cache?
   - **CLI `argparse` & Backward-Compatibility Collisions**: Does a proposed CLI flag or `nargs="+"` change conflict with existing flags (e.g., `--az-min`/`--az-max` sector bounds vs. post-hoc filters), break existing `bearing,x_frac` delimiter parsing, or fail because another argument is still `required=True`?
   - **Wall-Clock Timeout & Sandbox Traps**: Could proposed retry loops, increased timeouts, or larger DEM/API fetches exceed `WaitMsBeforeAsync: 10000` (10 seconds) and push synchronous commands into background tasks?
   - **Data Quality, Resolution, & Cache Poisoning**: Could querying new layers (e.g., unnamed waterbodies or `Locale` records) flood output caps with noise (swamps, ice masses, minor trail junctions), or could expanding spatial radius without scaling `grid_size` degrade DEM pixel resolution?
   - **Codebase Reality Mismatches**: Are line numbers, function names, or assumptions out of sync with the current code?
4. Categorize your findings into three clear sections, assigning lettered IDs (`A`, `B`, `C`, ...) to every flagged issue:
   - **1. Do NOT Apply As Written (Direct Workflow or Accuracy Regressions)**: Explain the exact technical failure mode and provide a concrete **Safe Alternative**.
   - **2. Proceed with Modifications (Moderate / Edge-Case Risks)**: Detail the exact engineering adjustment required to make the recommendation safe.
   - **3. Safe to Apply As-Is**: List all recommendations verified as safe and accurate.
5. Save your full scrutiny report to `<findings-dir>/recommendations_scrutiny.md` AND return the complete structured scrutiny report (`1`, `2`, `3` with lettered items `A, B, C, ...`) to the main agent so the user can review the verdicts.
```

#### 4.3 User Decision Gate & Generating `recommendations_final.md`
1. **Present Verdicts to User**: The Main Agent presents the Scrutiny Auditor's lettered findings (`A, B, C, ...`) and recommended safe alternatives/modifications to the user and asks for confirmation on how to proceed (unless the user explicitly requested autonomous end-to-end execution without pausing).
2. **Generate `<findings-dir>/recommendations_final.md` via Subagent**:
   - Once the user provides their verdicts (e.g., `"Accept all verdicts"` or `"A - Remove 6.1 Part 1; B,C - Incorporate safe alternative; D-H - Proceed with modifications"`), message the **Independent Scrutiny Auditor Subagent** via `send_message`:
     ```markdown
     User Verdicts on Scrutiny Items:
     <insert user verdicts>

     Please incorporate all approved adjustments directly into the consolidated recommendations and write the complete, updated specification document to `<findings-dir>/recommendations_final.md`. Preserve all file paths, line numbers, root-cause diagnostics, trajectory evidence, and exact code/prompt specifications, with every approved modification resolved directly inside the implementation specs. Reply with a concise summary of the incorporated adjustments and confirm the output path.
     ```
   - *(Having the Scrutiny Auditor Subagent write `recommendations_final.md` leverages its existing context and keeps the Main Agent's context window clean.)*

---

### Step 5: Component-Isolated Implementation & Independent Verification

Once `<findings-dir>/recommendations_final.md` is written, apply all recommendations and verify them using two distinct waves of subagents.

#### Step 5A: Component-Isolated Implementation Subagents
1. **Partition by File Ownership (Zero Cross-Agent Edits)**:
   - Review the Group structure of `<findings-dir>/recommendations_final.md` (if needed, read only lines `1–60` of `recommendations_final.md` for the Table of Contents).
   - Spawn one **Implementation Engineer Subagent** (`TypeName: "self"`) per target file or disjoint component group (e.g., Subagent 1: `<skill>/SKILL.md`; Subagent 2: `<skill>/scripts/geo_tools.py`; Subagent 3: `<skill>/scripts/extract_metadata.py`; Subagent 4: `<companion-skill>/SKILL.md` & `scripts/catalog_tools.py`).
   - **Strict Wave Sequencing Rule**: Never allow two concurrently running subagents to edit the same file. If a single large file (such as `geo_tools.py` or `SKILL.md`) is split across two subagents by architectural theme, run Wave 1 first, wait for the Wave 1 subagent on that file to finish, and then spawn the Wave 2 subagent on that file.
2. **Step 5A Subagent Prompt Template (Implementation Engineer)**:
   ```markdown
   Read `<findings-dir>/recommendations_final.md` (specifically <Group X: Sections X.1, X.2, ...> — use `ByteOffset=46080` if reading past 46,080 bytes) and apply ALL recommendations targeting `<target-file-path(s)>`.

   ### STRICT FILE OWNERSHIP (NO CROSS-AGENT EDITS):
   - You may ONLY edit: `<allowed-file-path(s)>`
   - Do NOT edit `<forbidden-file-paths>` — other subagents are editing those files concurrently.

   ### Implementation & Sanity-Check Requirements:
   1. Implement every requirement in `<Group X>` accurately, including all technical review safeguards.
   2. If editing a `SKILL.md` file, check its final byte size (`(Get-Item "<path>").Length`) and ensure it remains comfortably below the `46,080-byte` `read_file` truncation limit (ideally `< 42,000–44,000 bytes`) so future subagents can read `SKILL.md` in a single `read_file` call.
   3. If editing Python scripts, run syntax and CLI smoke tests (`python -m py_compile`, `--help`, and targeted subcommand tests with `BypassSandbox: true` if network/ExifTool is used), and clean up any temporary test files.
   4. Reply to the main agent with a concise summary of all changes applied and line numbers updated.
   ```

#### Step 5B: Independent Verification Subagents (Fresh Auditors)
Never rely solely on the implementation subagents' self-reports. Once Step 5A completes, spawn a **brand-new set of Independent Verification Auditor Subagents** (`TypeName: "self"`), one per target component/file group.

1. **Step 5B Subagent Prompt Template (Independent Verification Auditor)**:
   ```markdown
   You are an Independent Verification Auditor for `<target-file-path(s)>`.
   Read `<findings-dir>/recommendations_final.md` (in full — use `ByteOffset=46080` if the file exceeds 46,080 bytes, focusing on `<Group X: Sections ...>`) and inspect `<target-file-path(s)>`.

   ### STRICT AUDIT RULES:
   1. **Zero Trust**: Do NOT take anyone's word for it. Independently verify every single requirement and sub-requirement targeting `<target-file-path(s)>` line-by-line against `recommendations_final.md`.
   2. **Read-Only Audit First**: Do NOT edit skill or script files during your initial audit unless explicitly instructed to fix a discovered defect.
   3. **Live Execution & CLI Testing**:
      - For `SKILL.md`: Verify exact byte size (`< 46,080 bytes`), confirm a single `read_file` call reads line 1 to EOF with zero truncation, and verify exact line numbers for every required guardrail and template update.
      - For Python scripts: Run live CLI/Python tests (`BypassSandbox: true`) exercising every new/updated flag, edge case, cache behavior, error fallback, and `stdout` byte-size cap against real data. Clean up any temporary test files/directories you create.
   4. **Structured Verification Report**: Return a markdown table mapping every requirement ID (`1.1a`, `1.1b`, ...) to:
      - `Requirement Summary`
      - `Status` (`PASS`, `PARTIAL FAIL`, or `FAIL`)
      - `Verified File & Exact Line Numbers / Live Test Evidence`
   ```
2. **Remediation & Re-Verification Loop**:
   - If any Independent Verification Auditor reports a `PARTIAL FAIL` or `FAIL` (for example, an edge-case `stdout` byte cap exceeded at dense feature coordinates or a regex edge case), the Main Agent instructs a single active subagent to apply the targeted fix and re-run the live verification test until **100% of sub-requirements PASS**.
3. **Final Summary Report**:
   - The Main Agent presents the consolidated **Independent Verification Matrix** across all components to the user (and, when requested, generates a comprehensive git commit message or commits/pushes the changes).

---

## Mandatory Trajectory Evaluation Taxonomy (7 Core Dimensions)

Every Trajectory Inspector Subagent in Step 1 must evaluate its assigned trajectory(s) across all seven dimensions below, linking every finding to concrete trajectory evidence (`[<short-id>](conversation://<id>)`, step indices) and the specific flaw or omission in `SKILL.md` or `scripts/`:

### 1. Failed Tool Calls, Syntax Thrashing & Error-Recovery Loops
- **Host Shell & Quote-Escaping Failures**: `ParserError`, `SyntaxError`, or mangled arguments in `run_command` (nested quotes in inline `python -c "..."`, `exiftool`, or PowerShell escaping). *Fix*: Ban inline `python -c` and provide CLI flags in `scripts/`.
- **Blind Retry & Brute-Force Tinkering Loops**: 3+ consecutive tool calls mutating CLI flags (`argparse` `unrecognized arguments`, comma- vs. space-separated list args) or file paths by trial and error. *Fix*: Harden `argparse` (e.g., accept both `nargs="+"` space-separated and comma-separated args) and document exact CLI templates.
- **Environment, Path & Sandbox Assumptions**: `FileNotFoundError`, `PermissionError` inside the Windows sandbox (e.g., `WindowsApps\python.exe` blocked, network calls blocked, or `<appDataDir>/brain/` restricted when `BypassSandbox: false`). *Fix*: Unify `BypassSandbox: true` rules in `SKILL.md` so agents don't suffer 1-step sandbox failures.
- **Silent or Unhandled Tool Degradation**: Commands exiting `0` with empty/partial data (e.g., Overpass HTTP 200 soft runtime timeouts `"remark": "runtime error..."`, missing USGS layers, or un-retried tile export timeouts) that poison disk caches or trigger 30-step custom scripting spirals. *Fix*: Detect soft timeouts, add parallel authoritative fallbacks, and track per-provider completeness in cache files.

### 2. Unnecessary, Redundant & Inefficient Tool Calls
- **Trivial Print Probes & Mental-Math Spawns**: Running `python -c "print(...)"` for scalar unit conversions (`ft` to `m`, `°F` to `°C`) or simple arithmetic. *Fix*: Pre-compute dual units and deltas in `scripts/` and mandate internal mental math for simple scalars.
- **Unbatched / Fragmented Tool Execution**: Running `--overview` and `--box` in separate `crop_photo.py` calls, or calling `write_file` in one turn and `send_message` in the next turn (adding a 60k–85k token turn). *Fix*: Mandate single-call batching for crops and single-turn `write_file` + `send_message`.
- **Redundant File Reads & 2-Part `SKILL.md` Truncation Splits**:
  - If `SKILL.md` exceeds `46,080 bytes` (`read_file`'s per-call byte limit), 100% of subagents are forced to spend an extra turn calling `read_file` with `ByteOffset=46080`. *Fix*: Keep every `SKILL.md` under `< 42,000–44,000 bytes`.
  - Reading raw `<photo_stem>_skyline.json` or `<photo_stem>_nearby.json` via `read_file` (injecting `+15k` to `+66k` context tokens). *Fix*: Ban `read_file` on raw JSON dumps and provide fast CLI filter flags (`--types`, `--az-min`, `--az-max`, `--search`).
- **Ad-Hoc Script Reinvention**: Writing custom `.py` scripts in `scratch/` across $\ge 2$ trajectories for the same domain calculation (e.g., forward `x_frac -> bearing` projection, lunar ephemeris, JSON filtering). *Fix*: Promote recurring ad-hoc logic into `scripts/`.

### 3. Confirmation Bias, Anchoring & Motivated Reasoning
- **Premature Hypothesis Locking (Narrative Anchoring)**: Deciding what a feature/cause is *before* collecting objective data, or anchoring on an adjacent photo's location when EXIF GPS is `null` (`elapsed_time_min > 15`). *Fix*: Enforce geometry-first discovery, with an explicit exception to check `post.md` when EXIF GPS is missing and cannot be interpolated within `<= 15 minutes`.
- **One-Sided Information Retrieval**: Querying only one preconceived direction/name instead of a 360° candidate inventory.
- **Post-Hoc Rationalization of Contradictory Evidence**: Inventing excuses in `thinking` when tool output (solar azimuth/shadow angles, DEM line-of-sight occlusion, elevation) contradicts the model's hypothesis. *Fix*: Frame physical/geometric checks as **hard falsification gates** with zero rationalization permitted.
- **Uncorroborated Visual or Historical Extrapolation**: Hallucinating close-up botanical morphology from distant wide-angle shots, or inventing contradictory historical place-name etymologies, climber attributions, or dates from LLM memory without `post.md` or `search_web`.

### 4. Goal-Driven Obsession, Reward Hacking & Premature Completion
- **Satisficing & Silent Fabrication**: Hardcoding coordinates from LLM memory when a feature type (`PopulatedPlace`, `Lake`) is missing from tool output, or copying example peak elevations from `SKILL.md` (e.g., `University Peak 13,604 ft`) when absent from `post.md` and `--ref-elev-ft`. *Fix*: Add missing feature types to tools and de-bias hardcoded examples in `SKILL.md`.
- **Superficial Template Compliance vs. Constraint Neglect**: Violating editorial rules—such as using em-dashes `—` instead of spaced en-dashes ` – `, omitting dual units on spelled-out distances (`"twenty miles"`), grouping half-bare ranges (`0.5 – 3.9 mi / 0.8 – 6.3 km`), leaking internal image coordinates (`x = 0.65`), or using degree symbols (`°`). *Fix*: Add deterministic regex validation in `catalog_tools.py` / `scripts/` and explicit negative examples in `SKILL.md`.
- **Sycophancy & Uncritical Acceptance of Flawed Premises**: Failing to flag factual or directional discrepancies in the user's draft `post.md`.

### 5. Context Window Bloat, Compression Amnesia & Instruction Drift
- **Stdout Truncation & Context Pollution**: Tool `stdout` exceeding `~8–10 KB` gets mid-truncated by the `run_command` harness, hiding critical arrays (`dem_horizon_highpoints`, `foreground_ridges`, or `extract_metadata.py` fields when 25 `--ref-elev-ft` flags are passed) and triggering custom JSON-parsing scripts. *Fix*: Reorder highest-signal JSON keys first and compact `stdout` (`< 4.5–5.5 KB`) when `--out-json` is passed while writing the full dataset to disk.
- **Context Compression Amnesia**: After a context compression drop, the agent forgets procedural rules, re-reads `SKILL.md` repeatedly, or collapses subagent roles. *Fix*: Offload per-item work to isolated subagents and provide post-compaction recovery rules.
- **Late-Trajectory Constraint Decay & Shared-File Contention**: Repeatedly reading/editing a monolithic `catalog.md` across turns. *Fix*: Stage per-item outputs to isolated `scratch/<item>_entry.md` files and use atomic CLI `upsert` / `placements` subcommands.

### 6. Subagent Orchestration & Cross-Skill Handoff Pathologies
- **Under-Specified Delegation Prompts**: Parent agent omitting sequential context (`--prev-photo`, `--next-photo`, `--tcx`, corridor `--ref-elev-ft` benchmarks, or Windows `BypassSandbox: true` rules). *Fix*: Define a canonical **Subagent Invocation Prompt Template** in the parent skill.
- **System Prompt `<subagent_reminder>` vs. `SKILL.md` Contradictions**: Forbidding `send_message` in `SKILL.md` when the runtime injects `<subagent_reminder>` requiring `send_message`, or requiring `send_message` to duplicate a 6 KB markdown entry already written via `write_file`. *Fix*: Instruct subagents to call `write_file` and `send_message` in the same final turn, sending only a compact completion summary in `send_message`.
- **Cross-Skill Contradictions & Reviving Canceled Subagents**: Contradictory rules between parent and child skills, or messaging a user-canceled subagent instead of spawning a fresh subagent.
- **Polling Loops**: Calling `manage_task` or `manage_subagents` (`status`/`list`) in a loop instead of relying on reactive wakeup.

### 7. Skill Description Flaws, Ambiguities & User-Correction Signals
- **User Corrections as Ground-Truth Indictments**: Every user intervention in `User Turns` must be traced to the exact omission or ambiguity in `SKILL.md` or defect in `scripts/`.
- **Vague Directives vs. Deterministic Guardrails**: Replace subjective prose (*"be concise"*, *"check units"*) with hard caps, exact CLI templates, and automated pre-flight/post-flight script checks.
