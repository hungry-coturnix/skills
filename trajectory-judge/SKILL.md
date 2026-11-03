---
name: trajectory-judge
description: >-
  Evaluates all historical conversation and subagent trajectories where a given
  skill was used by orchestrating dedicated subagent inspectors per trajectory to
  prevent context compression in the main agent. Subagents write detailed findings
  to markdown files, allowing the main agent to read them, ask subagents for
  further analysis, synthesize cross-trajectory patterns, and diagnose LLM
  execution pathologies, failed tools, cognitive biases, and skill instruction
  ambiguities. Use whenever asked to judge, audit, or find improvements for a
  skill based on past trajectories.
---

# Trajectory Judge

Use this procedure to systematically audit historical trajectories (both parent orchestrator conversations and child subagent conversations) where a target skill was used, diagnose behavioral and execution pathologies, trace every failure mode back to flaws or ambiguities in the skill definition, and propose concrete, evidence-backed improvements to the skill.

---

## Distributed Subagent Inspection Architecture

Deep-dive transcript inspection across multiple long trajectories—reading model thinking traces, tool arguments, stdout/stderr, and context compression drops—requires tens of thousands of tokens per trajectory. Performing this inspection directly in the main orchestrator agent quickly leads to **context bloat and catastrophic context compression amnesia**, stripping away earlier trajectory details, dropping evaluation criteria, and blinding the agent to cross-trajectory patterns.

To maintain pristine context and maximize diagnostic depth, `trajectory-judge` uses a **distributed subagent orchestration pattern**:

```
                       +-------------------------------+
                       |          Main Agent           |
                       | (Lead Evaluator & Synthesizer)|
                       +---------------+---------------+
                                       |
          +----------------------------+----------------------------+
          | Spawns dedicated subagent per trajectory via invoke_subagent
          v                                                         v
+-----------------------------+                           +-----------------------------+
|    Subagent Inspector A     |                           |    Subagent Inspector B     |
| (Inspects Trajectory 1)     |                           | (Inspects Trajectory 2)     |
+--------------+--------------+                           +--------------+--------------+
               |                                                         |
               | Writes detailed findings                                | Writes detailed findings
               v                                                         v
+-----------------------------+                           +-----------------------------+
| findings/<conv-id-1>.md     |                           | findings/<conv-id-2>.md     |
+--------------+--------------+                           +--------------+--------------+
               ^                                                         ^
               | Reads markdown files for cross-trajectory synthesis     |
               +-----------------------+---------------------------------+
                                       |
                                       v
               +-----------------------------------------------+
               |    Main Agent Cross-Trajectory Analysis       |
               +-----------------------+-----------------------+
                                       |
                 Iterative follow-up   | send_message(Recipient=...)
                 queries & deep-dives  v
               +-----------------------------------------------+
               | Subagents perform further checks, update      |
               | findings files, and reply to Main Agent       |
               +-----------------------+-----------------------+
                                       |
                                       v
               +-----------------------------------------------+
               | Final Synthesis Report & Skill Improvements   |
               +-----------------------------------------------+
```

1. **Main Agent as Lead Evaluator**: Coordinates discovery, prioritizes a stratified sample, spawns dedicated subagent inspectors, reads the modular markdown findings, asks targeted follow-up questions via `send_message`, and writes the overarching evaluation report.
2. **Dedicated Subagent Inspector per Trajectory**: Each sampled trajectory is assigned to an independent subagent (`TypeName: "self"`). The subagent inspects the transcript using `scan_trajectories.py inspect`, audits against the 7 Core Evaluation Dimensions, and writes its full analysis to a dedicated markdown file.
3. **Persisted Markdown Findings Files**: Subagents write their comprehensive findings to disk (e.g., `<brainDir>/<current-conversation-id>/scratch/trajectory_findings/<target-conv-id>.md`). The main agent reads these compact, structured markdown summaries without loading megabytes of raw JSONL transcripts into its context.
4. **Interactive Cross-Trajectory Interrogation (`send_message`)**: When the main agent compares findings and notices cross-trajectory patterns, hypotheses, or discrepancies, it messages the subagents directly using `send_message` with targeted questions. The subagent performs the focused inspection in its existing context, updates its markdown file, and replies.
5. **Zero-Polling Reactive Wakeup**: The main agent launches subagents and waits reactively without polling loops (`manage_task` or `manage_subagents` status calls). The system automatically wakes up the main agent when subagents send messages.

---

## Helper Scripts

This skill includes a diagnostic scanner and transcript inspector in `scripts/scan_trajectories.py`:

- **Discover all trajectories for a skill** (maps parent ↔ subagent hierarchies, context token curves, context compression drops, tool counts, errors, inline Python, repeated file reads/edits, and user turns):
  ```powershell
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" discover --skill <skill-name> --executed-only
  ```
  *(Omit `--executed-only` if you also want to see trajectories where the skill was authored, edited, or previously judged.)*

- **Inspect diagnostic summary of a specific trajectory** (instantly prints total steps, context token curves, errors, compression drops, substantive user turns, and anomaly step numbers):
  ```powershell
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --summary
  ```

- **Inspect specific steps, thinking traces, or errors in a trajectory** (automatically reads `transcript_full.jsonl` when `--full` is passed to bypass `truncated_fields` without blowing up context):
  ```powershell
  # Inspect only steps with failed commands or errors:
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --errors-only --full

  # Inspect specific step ranges (including model thinking traces and tool calls):
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --steps 10-25 --full

  # Inspect only USER_INPUT and PLANNER_RESPONSE steps across a subagent trajectory:
  python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <uuid> --planner-only
  ```

---

## Evaluation Workflow

### Phase 1: Target Skill & Ecosystem Baseline Audit (Main Agent)
1. Read the target skill's `SKILL.md` and inspect any bundled files in `scripts/`, `references/`, `examples/`, or `resources/`.
2. Identify any **companion skills** referenced by or invoking the target skill (e.g., an orchestrator skill spawning child subagents). Read their `SKILL.md` files to check for cross-skill contract mismatches.
3. Extract the skill's intended contract:
   - Required inputs and assumptions about workspace/directory structure.
   - Prescribed sequence of phases, tools, and helper scripts.
   - Hard editorial, formatting, or verification constraints.
   - Orchestration boundaries (what runs in the main agent vs. subagents).

### Phase 2: Trajectory Discovery & Sample Selection (Main Agent)
1. Run `scan_trajectories.py discover --skill <skill-name> --executed-only` to locate all parent and subagent trajectories where the skill was used.
2. If the skill was delegated to subagents, run `discover` on both parent and child skills, or inspect `Subagents Spawned` from parent trajectories.
3. Prioritize a stratified sample of trajectories for deep subagent inspection:
   - **All trajectories with user corrections or interventions** (substantive `USER_INPUT` turns where the user challenged an answer, corrected a mistake, or questioned the workflow).
   - **All trajectories with failed tool calls, errors, or context compression drops**.
   - **Outlier trajectories** with abnormally high step counts, repeated file reads/edits, or heavy inline `python -c` usage.
   - **Chronological comparison** (earliest runs vs. recent runs) to verify whether past skill edits fixed prior issues or introduced regressions.
4. Define the **Findings Directory** where subagents will write their reports:
   - Default path: `<appDataDir>\brain\<current-conversation-id>\scratch\trajectory_findings\` (persisted and isolated).

### Phase 3: Spawning Dedicated Subagent Inspectors (Main Agent -> Subagents)
1. For each prioritized trajectory, spawn a dedicated subagent using `invoke_subagent`:
   - `TypeName`: `"self"` (inherits write and command tools).
   - `Role`: `Trajectory Inspector: <short-uuid>`
   - `Prompt`: Provide the populated **Subagent Invocation Prompt Template** below.
2. Record and maintain an in-memory mapping of:
   ```
   target_trajectory_id -> subagent_conversation_id -> findings_file_path -> status
   ```
3. **Reactive Wakeup**: Do NOT poll `manage_task` or `manage_subagents` in a loop. Stop calling tools and let the system automatically resume execution as subagents complete and send completion messages.

### Phase 4: Cross-Trajectory Reading & Pattern Synthesis (Main Agent)
1. As subagents complete (or once all prioritized trajectories have been inspected), read each markdown findings file using `read_file`.
2. Synthesize findings across the trajectories:
   - **Systemic Pathologies**: Are multiple trajectories failing on the same shell quoting issues, mental-math Python spawns, or confirmation bias patterns?
   - **Temporal Trends & Regressions**: Did recent trajectories improve or regress on constraints compared to earlier runs?
   - **Skill Defect Mapping**: Which specific sections, vague instructions, or missing guardrails in `SKILL.md` permitted or encouraged these behaviors?

### Phase 5: Interactive Clarification & Cross-Trajectory Follow-Up (Main Agent <-> Subagents)
1. When comparing findings across trajectories, the main agent may identify questions, unverified hypotheses, or discrepancies:
   - *Example*: Trajectory A had a clear shadow-angle rationalization in thinking at step 42. Did Trajectory B show a similar rationalization when identifying a mountain peak at step 28?
   - *Example*: Trajectory C suffered an unhandled API timeout. Did Trajectory D encounter a similar silent failure or execute a fallback?
2. The main agent messages the specific subagent using `send_message`:
   - `Recipient`: `<subagent_conversation_id>`
   - `Message`: State the specific question, the steps or hypothesis to investigate, and instruct the subagent to update its markdown findings file and reply with the answer.
3. The subagent inspects the requested steps/thinking traces in its existing context, appends or updates its markdown findings file, and replies to the main agent.

### Phase 6: Final Synthesis Report & Skill Improvement Proposal (Main Agent)
1. The main agent compiles the comprehensive Trajectory Judge Report following the standard output format.
2. Synthesizes findings under the 7 Core Evaluation Dimensions, linking every finding to concrete trajectory links (`[<id>](conversation://<id>)`), step indices, and quotes from subagent findings files.
3. Proposes ready-to-apply diffs/edits for `SKILL.md` and any companion helper scripts.

---

## Subagent Invocation Prompt Template

When the main orchestrator agent invokes a subagent inspector via `invoke_subagent`, use the following prompt structure:

```markdown
You are a dedicated Trajectory Inspector auditing a historical Antigravity conversation where the skill `<skill-name>` was used.

### Target Trajectory Metadata:
- **Conversation ID**: <target-conversation-id>
- **Parent Conversation ID**: <parent-id-or-None>
- **Timestamps**: <created_at> to <ended_at>
- **Target Skill**: <skill-name> (<path-to-skill-SKILL.md>)
- **Findings Output File**: <absolute-path-to-findings-file>
  (e.g., C:\Users\Tilman\.gemini\antigravity\brain\<current-conversation-id>\scratch\trajectory_findings\<target-conversation-id>.md)

### Key Diagnostic Signals from Discovery:
- Total Steps: <step-count> | Context Tokens: <first-tokens> -> <peak-tokens>
- Flagged Errors / Exit Codes: <error-step-list>
- Context Compression Events: <compression-events-list>
- Substantive User Turns / Corrections: <user-turn-step-list>
- Top Repeated Reads / Edits: <repeated-reads-summary>

### Your Inspection Mission:
1. Run diagnostic inspection on the target trajectory:
   python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <target-conversation-id> --summary
2. Inspect the critical steps around every user correction, error, compression boundary, and high-impact decision:
   python "C:\Users\Tilman\.gemini\config\skills\trajectory-judge\scripts\scan_trajectories.py" inspect --conversation-id <target-conversation-id> --steps <step-range> --full
3. Examine the `thinking` trace of `PLANNER_RESPONSE` steps to uncover the agent's internal rationale, confirmation biases, or amnesia.
4. Audit the trajectory rigorously against the Mandatory 7 Core Evaluation Dimensions:
   1. Failed Tool Calls, Syntax Thrashing & Error-Recovery Loops
   2. Unnecessary, Redundant & Inefficient Tool Calls
   3. Confirmation Bias, Anchoring & Motivated Reasoning
   4. Goal-Driven Obsession, Reward Hacking & Premature Completion
   5. Context Window Bloat, Compression Amnesia & Instruction Drift
   6. Subagent Orchestration & Cross-Skill Handoff Pathologies
   7. Skill Description Flaws, Ambiguities & User-Correction Signals
5. Write your complete, detailed findings report to the specified markdown file:
   `<absolute-path-to-findings-file>`
   Follow the Standard Individual Trajectory Findings Template.
6. Once the findings file is written, respond to the main orchestrator agent with:
   - Confirmation of the file path written.
   - A concise (1-2 paragraph) executive summary of the primary pathologies and root causes identified.
   - A list of any open questions or suspicious steps that may warrant cross-trajectory comparison.
```

---

## Standard Individual Trajectory Findings Template

Each subagent must format its findings markdown file using the following structure:

```markdown
# Trajectory Inspection Report: `<conversation-id>`

- **Target Skill**: `<skill-name>`
- **Conversation Link**: [<short-id>](conversation://<conversation-id>)
- **Parent Conversation**: [<short-parent-id>](conversation://<parent-id>) *(if applicable)*
- **Created**: <ISO timestamp> | **Ended**: <ISO timestamp>
- **Steps**: <total-steps> | **Peak Context**: <peak-tokens> tokens | **Compression Drops**: <count>
- **Error Steps**: <list-of-step-numbers>
- **Substantive User Turns**: <count>

---

## 1. Executive Summary & Trajectory Arc
[Concise narrative summary of what the agent was asked to do, how it executed the task, where it stumbled, and whether it succeeded, satisficed, or required user intervention.]

---

## 2. Context Token Curve & Compression Events
- Initial Context: `<tokens>`
- Peak Context: `<tokens>`
- Compression Events:
  - Step `<index>`: Context dropped from `<before>` to `<after>` (drop of `<delta>` tokens).
- Procedural Forgetting or Behavioral Shifts Observed After Compression:
  - [Detail any rule violations, repeated reads of SKILL.md, or orchestration shifts that occurred immediately after compression.]

---

## 3. Substantive User Interventions & Corrections
[For every non-routine user prompt, analyze what the user corrected, why the agent made the mistake, and what was missing in SKILL.md.]
- **Step `<index>`**:
  - *User Message*: `<quote>`
  - *Context / Trigger*: [What the agent did right before this]
  - *Agent's Reasoning*: [Quote from thinking trace showing why it made the error]
  - *Skill Root Cause*: [Exact omission or ambiguity in SKILL.md]

---

## 4. Evaluation Across the 7 Core Dimensions

### Dimension 1: Failed Tool Calls, Syntax Thrashing & Error Recovery
- [Findings with step numbers, command snippets, stderr, and whether brute-force tinkering occurred.]

### Dimension 2: Unnecessary, Redundant & Inefficient Tool Calls
- [Inline python -c math calls, unbatched API calls, repeated file reads of monolithic files.]

### Dimension 3: Confirmation Bias, Anchoring & Motivated Reasoning
- [Premature hypothesis locking, post-hoc rationalization in thinking traces, failure to falsify.]

### Dimension 4: Goal-Driven Obsession, Reward Hacking & Premature Completion
- [Silent fabrication, constraint neglect, sycophancy, skipping negative constraints.]

### Dimension 5: Context Bloat, Compression Amnesia & Instruction Drift
- [Context pollution, late-trajectory rule decay, lost-in-the-middle phenomena.]

### Dimension 6: Subagent Orchestration & Cross-Skill Handoff Pathologies
- [Bare invocation prompts, role collapse, cross-skill contradictions, polling calls.]

### Dimension 7: Skill Description Flaws & Ambiguities
- [Direct mapping from observed errors to vague or missing instructions in SKILL.md.]

---

## 5. Key Excerpts (Thinking Traces & Tool Calls)
```markdown
[Step <idx> - Thinking Trace Excerpt]
...
[Step <idx> - Tool Call & Output]
...
```

---

## 6. Recommended Skill Fixes (Local Perspective)
[Concrete additions, guardrails, or script utilities that would have prevented the failures in this trajectory.]
```

---

## Interactive Cross-Trajectory Follow-Up Guidelines

When the main agent reviews multiple findings files in Phase 4, it will often formulate hypotheses that require deeper inspection of a specific subagent's trajectory.

### When to Message a Subagent:
- **Hypothesis Testing**: A pathology was detected in Trajectory A (e.g., model assumed solar azimuth was wrong when photo shadow didn't match its candidate peak). The main agent tasks Subagent B to check whether Trajectory B's thinking at step 34 showed the same rationalization.
- **Ambiguity Clarification**: Subagent C's findings noted a command failure at step 12, but didn't determine whether the failure was due to PowerShell quote-escaping or missing file paths.
- **Cross-Run Inconsistency**: Trajectory E followed a rule strictly, while Trajectory F ignored it completely under identical conditions. The main agent asks Subagent F to inspect what in the user prompt or context distracted the model.

### Messaging Format (`send_message`):
```markdown
Recipient: <subagent_conversation_id>
Message:
"Cross-Trajectory Follow-Up Query:
In reviewing findings across trajectories, we found that Trajectory <other-id> exhibited <pathology>.
Please inspect steps <X-Y> of your trajectory (<target-conversation-id>) and check the model's thinking trace for:
1. <Specific question 1>
2. <Specific question 2>
Please update your findings markdown file (<findings-file-path>) with this additional evidence and reply with your findings."
```

### Subagent Follow-Up Responsibilities:
1. Run targeted inspection (`scan_trajectories.py inspect --steps X-Y --full`) in its existing conversation session.
2. Edit its existing markdown findings file using `edit_file` to append an `## Addendum: Cross-Trajectory Follow-Up Analysis` section with the new findings.
3. Message the main agent back with a direct answer to the query and confirmation that the findings file has been updated.

---

## Mandatory Trajectory Evaluation Taxonomy (7 Core Dimensions)

Evaluate every examined trajectory across all seven dimensions below. Categorize every finding by its specific sub-type and link it to concrete trajectory evidence (`[conversation-id](conversation://<id>)`, step numbers) and the specific flaw or ambiguity in `SKILL.md`.

### 1. Failed Tool Calls, Syntax Thrashing & Error-Recovery Loops
LLMs driven by goal completion frequently brute-force failing commands rather than diagnosing the environment or tool contract.
- **Host Shell & Quote-Escaping Failures**:
  - *Signals*: `ParserError`, `SyntaxError`, or mangled arguments in `run_command` (especially nested double/single quotes in inline `python -c "..."`, `exiftool` format strings, or `\` vs. PowerShell `` ` `` escaping).
  - *Skill Diagnosis*: Does the skill lack file-first scripting rules, PowerShell-safe command templates, or pre-packaged Python wrappers in `scripts/`?
- **Blind Retry & Brute-Force Tinkering Loops**:
  - *Signals*: 3+ consecutive tool calls mutating CLI flags, file paths, or regexes by trial and error after a failure.
  - *Skill Diagnosis*: Does the skill omit exact command syntax, required flags, or deterministic file location rules?
- **Environment, Path & Dependency Assumptions**:
  - *Signals*: `FileNotFoundError`, `ModuleNotFoundError`, wrong working directory assumptions (remembering that `run_command` sessions do not persist `cd` across calls), or unverified input paths.
  - *Skill Diagnosis*: Does the skill fail to specify absolute path patterns, pre-flight existence checks, or standard-library-only script constraints?
- **Silent or Unhandled Tool Failures**:
  - *Signals*: Commands returning exit code `0` with error text or empty results (e.g., REST API timeouts, empty GNIS/EPQS responses, missing EXIF tags) where the agent proceeds as if the call succeeded.
  - *Skill Diagnosis*: Does the skill lack explicit fallback procedures and validation checks for empty/degraded tool outputs?

### 2. Unnecessary, Redundant & Inefficient Tool Calls
Goal-obsessed models often use tools as an external scratchpad or execute fragmented micro-steps instead of batched operations.
- **Trivial Print Probes & Mental-Math Spawns**:
  - *Signals*: Executing `python -c "print(...)"` solely to convert scalar units (e.g., feet to meters, Fahrenheit to Celsius), format strings, or echo variables already in context.
  - *Skill Diagnosis*: Does the skill fail to mandate internal mental math for simple scalar conversions or provide a single script that outputs pre-formatted dual units?
- **Unbatched / Fragmented Tool Execution**:
  - *Signals*: Running multiple sequential `exiftool`, `read_file`, or API calls on the same target to extract fields one by one instead of retrieving all needed fields in a single invocation.
  - *Skill Diagnosis*: Should the skill consolidate multi-step data gathering into a single atomic helper script in `scripts/`?
- **Redundant File Reads & State Re-Discovery**:
  - *Signals*: High counts in `Top Repeated File Reads` (reading the same `post.md`, `catalog.md`, or `SKILL.md` 5–25+ times in a single conversation).
  - *Skill Diagnosis*: Is the workflow forcing the agent to re-read monolithic files on every turn instead of working from structured deltas or modular files?
- **Ad-Hoc Script Reinvention**:
  - *Signals*: Writing custom one-off Python scripts in `scratch/` across multiple trajectories to perform the exact same domain logic (e.g., parsing TCX/GPX tracks, computing Haversine distance/bearing, querying elevation APIs).
  - *Skill Diagnosis*: Any domain calculation written ad-hoc in $\ge 2$ trajectories should be promoted into a reusable, tested utility in the skill's `scripts/` folder.

### 3. Confirmation Bias, Anchoring & Motivated Reasoning
When an LLM forms an early hypothesis (from user draft text, narrative proximity, or an initial guess), it exhibits strong cognitive bias toward confirming that hypothesis rather than falsifying it.
- **Premature Hypothesis Locking (Narrative Anchoring)**:
  - *Signals*: In `thinking`, the model decides what a subject/feature/cause is *before* running discovery tools ("This photo was taken near X, so the peak in the background must be Y"), then uses tools only to look up facts about Y.
  - *Skill Diagnosis*: Does the skill allow hypothesis generation before objective data collection? Enforce **data-first, hypothesis-second** ordering in `SKILL.md`.
- **One-Sided Information Retrieval**:
  - *Signals*: Querying coordinates, features, or code references in only one preconceived direction/quadrant or searching only for a single expected name rather than performing an omnidirectional (360°) or exhaustive candidate search first.
  - *Skill Diagnosis*: Does the skill require an unbiased candidate inventory (e.g., running a 360° nearby-feature query) before evaluating individual candidates?
- **Post-Hoc Rationalization of Contradictory Evidence**:
  - *Signals*: Tool output contradicts the agent's hypothesis (e.g., solar azimuth/shadow angles point the opposite way, elevation is higher than the assumed peak, a ridge blocks line-of-sight, or a search returns no results for an assumed place name), and the `thinking` trace invents excuses ("Perhaps the shadow is cast by a local rock", "Maybe the camera clock or GPS was off") instead of rejecting the hypothesis.
  - *Skill Diagnosis*: Does the skill frame physical, geometric, or factual checks as **hard falsification gates** (invariants that immediately disqualify a candidate, with zero rationalization permitted)?
- **Uncorroborated Visual or Factual Extrapolation**:
  - *Signals*: Identifying distant peaks, fine botanical/zoological species details, or trail junctions from downsampled full-frame images without cropping, or asserting unverified historical/geological claims not backed by search or authoritative sources.
  - *Skill Diagnosis*: Does the skill mandate cropping (`crop_photo.py`) for distant/small subjects and cross-checking sympatric lookalikes or official map nomenclature?

### 4. Goal-Driven Obsession, Reward Hacking & Premature Completion
LLMs optimize heavily for producing a response that *looks* like the requested deliverable, sometimes cutting corners on substance or constraints.
- **Satisficing & Silent Fabrication**:
  - *Signals*: When a tool fails, times out, or returns incomplete data, the agent silently invents plausible-looking numbers, coordinates, or facts to fill out the required output template rather than repairing the tool call or explicitly marking `[Not available]`.
  - *Skill Diagnosis*: Does the skill explicitly prohibit silent estimation when deterministic tools are available, and specify exact fallback placeholders when data is genuinely absent?
- **Superficial Template Compliance vs. Constraint Neglect**:
  - *Signals*: The agent satisfies the high-level structure of the output but violates negative or editorial constraints (e.g., introducing subheadings when forbidden, using em-dashes `—` instead of spaced en-dashes ` – `, omitting metric conversions `XXX ft / YYY m` or `XX°F / YY°C`, or dropping material details from the initial draft).
  - *Skill Diagnosis*: Are editorial constraints buried in prose instead of highlighted as a mandatory pre-completion verification checklist?
- **Sycophancy & Uncritical Acceptance of Flawed Premises**:
  - *Signals*: Failing to catch or flag factual errors in the user's initial draft (e.g., wrong trailhead names, misidentified wildlife, inaccurate elevations) or hallucinating callbacks to hikes the user never took.
  - *Skill Diagnosis*: Does the skill instruct the agent to independently verify every proper noun, trailhead, geographic claim, and cross-post callback against workspace files and external ground truth?

### 5. Context Window Bloat, Compression Amnesia & Instruction Drift
Long-running or multi-item trajectories suffer from severe degradation as context grows and undergoes automatic context compression.
- **Context Pollution & Truncation Events**:
  - *Signals*: Rapid escalation in `Context tokens (in+cache)` and high counts of `Truncated Steps` (`truncated_fields: ["content"]`) caused by dumping verbose command outputs, unfiltered web searches, or reading huge files without line bounds.
  - *Skill Diagnosis*: How can tool outputs be made compact and structured so raw noise never enters the context window?
- **Context Compression Amnesia (Procedural Forgetting)**:
  - *Signals*: After one or more `Context Compression Drops` (where total context tokens drop sharply, e.g., from >80k–110k down to 25k–55k), the agent forgets key procedural rules from `SKILL.md`—such as suddenly performing photo analysis directly in the main orchestrator agent instead of spawning subagents, or having to re-read `SKILL.md` repeatedly (`Top Repeated File Reads`).
  - *Skill Diagnosis*:
    - Does the skill accumulate too many items/turns in a single monolithic session without re-anchoring instructions?
    - Can heavy per-item work be strictly isolated inside stateless subagents so the parent orchestrator's context remains tiny and never triggers compression?
- **Late-Trajectory Constraint Decay ("Lost in the Middle")**:
  - *Signals*: Early outputs in a trajectory strictly follow all rules, while later outputs in the same trajectory progressively drop constraints (e.g., forgetting GPS coordinates in captions, slipping back to em-dashes, skipping verification steps).
  - *Skill Diagnosis*: Add a mandatory **Final Audit / Self-Check Phase** at the end of `SKILL.md` that forces the agent to scan its final output (e.g., via regex/script or checklist) for constraint violations before concluding.
- **Monolithic Shared-File Contention (`catalog.md` / Shared State Anti-Pattern)**:
  - *Signals*: Subagents or sequential turns repeatedly reading and editing a single growing shared file (e.g., `catalog.md` edited 15–25+ times), causing line-range mismatches, context bloat, and redundant file reads.
  - *Skill Diagnosis*: Subagents must return structured results in their final response message rather than reading/appending to shared monolithic files; the orchestrator should batch or manage state updates cleanly.

### 6. Subagent Orchestration & Cross-Skill Handoff Pathologies
When a workflow spans a parent orchestrator and child subagents, ambiguities at the boundary cause duplicated work or lost context.
- **Under-Specified Delegation Prompts**:
  - *Signals*: Inspecting `invoke_subagent` calls reveals that the parent only passed a bare filename (e.g., `Analyze 281A5105.jpg`) without passing prior sequential context that only the parent knows (such as the previous photo's GPS coordinates/timestamp for travel-vector calculation, hike route context, or specific output instructions).
  - *Skill Diagnosis*: Does the parent skill define an explicit **Subagent Invocation Prompt Template** listing every required parameter the parent must pass to the subagent?
- **Role Collapse & Duplicate Execution**:
  - *Signals*: Either the parent agent performs the subagent's analysis itself (bypassing `invoke_subagent`), or the parent spawns the subagent *and* redundantly runs the same metadata/analysis commands in the parent workspace.
  - *Skill Diagnosis*: Are the division of labor and orchestration boundaries explicitly enforced in *both* the parent skill and child skill?
- **Cross-Skill Contradictions**:
  - *Signals*: Parent skill `SKILL.md` instructs the agent/subagent to do X (e.g., *"spawn a subagent to run the `photo-analysis` skill on the image and append the resulting analysis to `<date>/catalog.md`"*), while child skill `SKILL.md` states the exact opposite (*"Subagents analyzing photos must never read or append directly to a shared, monolithic `catalog.md`"*).
  - *Skill Diagnosis*: Always cross-audit linked skills and eliminate contradictory instructions across their boundaries.
- **Polling Loops**:
  - *Signals*: Non-zero `Polling / Status Calls` (`manage_task` or `manage_subagents` with `status`/`list` in a loop) instead of relying on reactive wakeup.
  - *Skill Diagnosis*: Add an explicit rule reminding the agent not to poll background tasks or subagents.

### 7. Skill Description Flaws, Ambiguities & User-Correction Signals
Every trajectory anomaly must be synthesized into a concrete critique of the skill description (`SKILL.md`).
- **User Corrections as Ground-Truth Indictments**:
  - Treat every user correction or follow-up question in `User Turns` as a high-priority failure signal. Ask: *What was missing, vague, or misleading in `SKILL.md` that allowed the mistake the user had to manually correct?*
- **Workflow & Phase-Transition Ambiguities**:
  - Identify places where `SKILL.md` is ambiguous about *when* actions occur (e.g., whether narrative updates happen after each photo arrives one-by-one vs. only after all photos are analyzed; whether Phase 1 proofing edits are applied immediately to `post.md` or proposed for approval first).
- **Vague Directives vs. Verifiable Rules**:
  - Flag soft, subjective phrasing in `SKILL.md` (e.g., *"double check the draft for factual correctness"*, *"suggest additional facts"*) that lacks concrete verification mechanisms or quality guardrails.
- **Missing Pre-Flight / Post-Flight Verification Gates**:
  - Identify where adding a deterministic script check (e.g., checking for forbidden `—` characters, missing dual units, or broken placeholders) would catch 100% of formatting regressions automatically.

---

## Output Format for the Evaluation Report

Always present your evaluation in a comprehensive, structured report (either directly or in an artifact for extensive multi-trajectory analyses) following this template:

```markdown
# Trajectory Judge Report: `<skill-name>`

## 1. Executive Summary & Trajectory Inventory
- **Target Skill**: [SKILL.md](file:///path/to/skill/SKILL.md) (and any linked companion skills audited)
- **Findings Files**: List paths to all generated subagent findings markdown files.
- **Trajectories Evaluated**:
  - List parent and subagent trajectories with clickable links (`[<short-id>](conversation://<conversation-id>)`), date, step count, peak context tokens, compression drops, error count, and classification.
- **High-Level Assessment**: 1-paragraph synthesis of the primary failure modes and the highest-leverage skill improvements across all evaluated trajectories.

## 2. Categorized Cross-Trajectory Findings
Group synthesized findings under the 7 Evaluation Dimensions. For each finding, include:
- **Category & Pathology Name** (e.g., *Dimension 3: Confirmation Bias – Post-Hoc Shadow Rationalization* or *Dimension 5: Context Compression Amnesia – Subagent Bypass*)
- **Cross-Trajectory Pattern**: Explain how this pattern appeared across multiple inspected trajectories (citing individual subagent findings).
- **Observed Evidence**: Cite specific trajectories (`[<id>](conversation://<id>)`), step indices (`Step X–Y`), tool calls, user corrections, or `thinking` trace excerpts.
- **Root Cause in Skill Description**: Quote or reference the exact lines (or omission) in `SKILL.md` (or companion skills) that caused or permitted this behavior.
- **Impact**: Token waste, latency, factual errors, formatting regressions, or user friction.

## 3. Flaws, Contradictions & Ambiguities in Current `SKILL.md`
A concise, prioritized table mapping each identified flaw in the current skill text to its proposed fix:
| Priority | Flaw / Ambiguity in `SKILL.md` | Section / Lines | Observed Consequence in Trajectories | Proposed Fix |
| :--- | :--- | :--- | :--- | :--- |

## 4. Recommended Skill & Tooling Improvements
Provide concrete, ready-to-apply solutions:
1. **`SKILL.md` Revisions**: Exact markdown blocks or unified diffs showing the proposed wording additions, structural reorganizations, subagent prompt templates, and verification checklists.
2. **Helper Script Additions / Updates (`scripts/`)**: Specifications or code for any new/updated helper scripts to eliminate failed tool calls, ad-hoc script thrashing, or manual formatting checks.
3. **Companion Skill Alignment** (if applicable): Fixes for any cross-skill contradictions discovered during the audit.
```
