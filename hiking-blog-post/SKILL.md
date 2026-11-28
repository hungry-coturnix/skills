---
name: hiking-blog-post
description: >-
  Curates an initial blog post draft and augments it with additional
  information. Incorporates photos into the draft with additional narrative
  and photo captions.
---

# Hiking Blog Post

Use this procedure to editorialize a hiking blog post, taking an initial draft
and thoroughly critiquing it, then augmenting it with additional information.
Places photos in the draft and updates the narrative.

## Directory Structure

Each blog post lives in its own date-stamped folder:

```text
<date>/               # e.g., 2026-10-02/
├── post.md           # The working blog post draft
├── catalog.md        # Compiled photo analyses and captions
└── photos/           # Image files (e.g. 281A5017.jpg)
```

## Phase 1: Ingestion and Proofing

All blog posts follow the same procedure. You will be given a date in the
format YYYY-MM-DD (which we will refer to as `<date>` in the instructions below)
and the initial draft of the blog post. Save the draft to `<date>/post.md`. You
can make any subsequent changes to the draft inline in this file.

Please double check the draft for factual correctness and flag anything that
may be inaccurate. Please correct any language mistakes (e.g. spelling,
grammar, etc.). List all changes you have made for reference.

- **Immediate Disk Persistence First (`write_file` Before Any Research)**: Your **very first tool call** in Phase 1 (immediately after reading `SKILL.md`) MUST be `write_file` to persist the proofed draft (with language fixes and dual-unit conversions applied) to `<workspace>/<date>/post.md`. Do **NOT** run `Get-ChildItem`, `read_file` on past `<YYYY-MM-DD>/post.md` files, or `search_web` before `<date>/post.md` is saved to disk. If subsequent Phase 1 fact-checking uncovers a factual correction that should be applied directly to the draft, update `<date>/post.md` inline via `edit_file`.
- **No Premature Current-Hike Directory Probing**: Do **NOT** run `Get-ChildItem`, `dir`, or `Test-Path` on `<date>/` or `<date>/photos/` during Phase 1 or Phase 2—`write_file` creates `<date>/post.md` automatically, and photos are provided later in Phase 3. (Listing `<YYYY-MM-DD>` folders in the workspace root during Phase 2 to discover past hikes is permitted *after* `<date>/post.md` has been written.)

## Phase 2: Narrative Augmentation

After `<date>/post.md` is persisted to disk, suggest any additional facts that
we might want to incorporate into the post, for example facts about the
history, geography, geology, etc. for natural features, or details about the
species, evolution, habitat, behavior, etc. for references to fauna or flora.
Scan existing `<YYYY-MM-DD>/post.md` folders in the workspace to identify
relevant callbacks to past hikes (avoiding duplication while highlighting
continuity). Stay strictly within the active workspace directory (never scan
parent directories `..`).

- **Focused Web Research (No `site:` Operators or Retry Loops)**: When using `search_web` in Phase 1 or Phase 2, never use restrictive `site:` operators (which can hang and fail with empty responses), and limit research to at most 1–2 queries per landmark or topic—if a detail (such as an exact etymology) is unrecorded after 1–2 searches, move on immediately.
- **Never Guess Landmark Elevations from LLM Memory**: When adding dual-unit elevations to `post.md` in Phase 2 or compiling `--ref-elev-ft` benchmarks in `scratch/subagent_prompt_template.txt` at the start of Phase 3, **never** guess peak, lake, or pass elevations from LLM memory (which can hallucinate elevations off by `> 1,000 ft`). Every elevation added to `post.md` or `--ref-elev-ft` that was not in the user's draft MUST be verified first via `search_web` or a single `geo_tools.py nearby-features <start_lat> <start_lon> --radius-km 25.0` lookup (`BypassSandbox: true`).

## Phase 3: Photo Processing

After the initial draft, you will get a set of photos, one by one, that were
taken during the hike. You will only be provided with a filename. All photos
are located in the directory `<date>/photos`.

### 1. Strict Delegation Boundary & Anti-Amnesia Guardrails
- **No Parent Photo Analysis**: Do **not** view photos (`read_file`), run `extract_metadata.py` / `crop_photo.py` / `geo_tools.py`, run `Test-Path` checks, or perform independent geographic/flora verification in the main orchestrator agent. Immediately delegate each photo to a `self` subagent running the `photo-analysis` skill, and let the user verify the subagent's output.
- **Persist the Hike-Specific Subagent Prompt Template & Complete `--ref-elev-ft` Corridor Benchmarks to Disk**: At the start of Phase 3 (before spawning the first photo subagent), save your hike-specific subagent prompt template (with the hike's `<workspace>`, `<date>`, and pre-computed `--ref-elev-ft "Label:ElevFt"` benchmark flags filled in, plus a comment header summarizing the Parent 4-Step Lifecycle rules below) to `<workspace>/scratch/subagent_prompt_template.txt` using `write_file` (`Overwrite: true` — never reuse a stale `subagent_prompt_template.txt` from an earlier session or skill version without refreshing it against the latest `SKILL.md`). Ensure the shared `--ref-elev-ft` list includes not only on-trail stops, lakes, and passes from `post.md`, but also all prominent corridor and distant cross-valley horizon summits mentioned in `post.md` or framing the route (verified via `search_web` or `geo_tools.py`, never from LLM memory) so sibling subagents use identical benchmark elevations across all photos.
- **Post-Compaction Recovery Rules**: If context compaction occurs during Phase 3, immediately re-read **both** `<workspace>/scratch/subagent_prompt_template.txt` **AND** `C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\SKILL.md` (lines 50–105) upon waking so your subagent prompts and Parent 4-Step Lifecycle rules never degrade.
- **No Per-Photo `validate` Calls in Phase 3**: Do **NOT** run `catalog_tools.py validate` after individual photos in Phase 3—`upsert` already runs entry formatting checks on each photo and returns `formatting_issues` and `normalized_entry`. Run `validate` **ONLY once** in Phase 4 after inserting all `[PHOTO: ...]` tags into `post.md`.
- **No Transcript Scavenging or Inline `python -c` Scripts**: Never read or grep `.system_generated/logs/transcript.jsonl` or `transcript_full.jsonl` after context compaction, and never run inline `python -c` one-liners to parse or update `<date>/catalog.md`. To check whether the current photo was already upserted before a mid-turn context compaction, run `python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py status "<workspace>/<date>"` (`BypassSandbox: true`)—never inspect `transcript.jsonl`.

### 2. Canonical Subagent Prompt Template
Pass this exact prompt structure (filling in `<workspace>`, `<date>`, `<filename>`, `<photo_stem>`, and the hike's `--ref-elev-ft` flags) to `invoke_subagent`:

```text
Analyze the hiking photo `<workspace>/<date>/photos/<filename>` using the `photo-analysis` skill:

1. MANDATORY FIRST STEP: Read `C:\Users\Tilman\.gemini\config\skills\photo-analysis\SKILL.md` in full and follow all instructions, geometric/solar verification guardrails, and output formatting rules before running any other tools.
2. Stage 1 (Unbiased Geometric Discovery — Before Reading `post.md`):
   - On Windows PowerShell 5.1, NEVER use `&&` (which fails with `ParserError`), and do NOT chain multiple `geo_tools.py` or `crop_photo.py` subcommands with `;` in a single `run_command` (which causes >10s backgrounding and `stdout` truncation). Issue separate parallel `run_command` tool calls in the same turn (always passing `BypassSandbox: true` and `WaitMsBeforeAsync: 10000` on the very first attempt for ALL `python` skill script invocations: `extract_metadata.py`, `crop_photo.py`, and ALL `geo_tools.py` subcommands, both network and local).
   - Run `extract_metadata.py` (`BypassSandbox: true`) with the hike's reference elevation benchmarks:
     `python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\extract_metadata.py "<workspace>/<date>/photos/<filename>" --ref-elev-ft "<Label1>:<ElevFt1>" --ref-elev-ft "<Label2>:<ElevFt2>"`
   - View the photo (`read_file`) and run `geo_tools.py` (`sun-position`, `nearby-features`, `horizon-skyline --out-pano "<workspace>/scratch/<photo_stem>_pano.png" --out-json "<workspace>/scratch/<photo_stem>_skyline.json"`, and `render-topo --out "<workspace>/scratch/<photo_stem>_topo.png"` — passing `--radius-km 40.0` to both `nearby-features` and `horizon-skyline` for broad cross-valley views, and invoking each subcommand as a separate parallel `run_command` call with `BypassSandbox: true`) and `crop_photo.py --grid` (`BypassSandbox: true`, after reading `extract_metadata.py`) to establish the true physical camera orientation and visible features.
   - Prefix ALL temporary files saved in `<workspace>/scratch/` with `<photo_stem>_`.
3. Stage 2 (Narrative Cross-Check & Draft Placement):
   - After establishing your geometric orientation in Stage 1, read `<workspace>/<date>/post.md` to cross-check trail/junction context, test any landmarks mentioned near this segment, and determine the exact `Suggested Draft Placement`.
4. SINGLE-WRITER RULE & SINGLE-TURN DELIVERY: Do NOT read, grep, or modify `<workspace>/<date>/catalog.md`. Format your output strictly using the 4-bullet `## Output Format` template in `photo-analysis/SKILL.md`. Verify all formatting rules (plain-text dual units, zero code backticks around measurements, spaced en-dashes ` – `, zero angle `°` symbols, single elevation per bold header) before calling `write_file` + `send_message`. Invoke `write_file` (`Overwrite: true`, writing your complete markdown entry `### Photo: \`<filename>\`` through `### Suggested Draft Placement` to `<workspace>/scratch/<photo_stem>_entry.md`) and `send_message` **in the SAME tool-call turn**. In `send_message`, transmit ONLY a compact completion summary (the exact absolute file path `<workspace>/scratch/<photo_stem>_entry.md`, the 1-line `Location`, `Suggested Caption`, and `Suggested Draft Placement` summary, plus any draft discrepancy note) rather than duplicating the entire 5–7 KB markdown entry. Once `send_message` is called, your final turn MUST contain **zero tool calls** (never call `write_file` or `edit_file` a second time after `send_message`, as `send_message` immediately triggers parent `upsert`), ending with a 1-sentence confirmation without repeating the markdown block.
```

### 3. 4-Step Per-Photo Lifecycle & Subagent Reuse Protocol
For each photo `$N$` provided by the user, follow this exact 4-step lifecycle:

- **Step A (Invoke Subagent)**: Spawn the `photo-analysis` subagent (`TypeName="self"`) for Photo `$N$` using the canonical prompt template above.
- **Step B (Immediate Disk Upsert, Single-Writer `catalog.md` Rule & User Presentation)**: As soon as the subagent completes and writes `<workspace>/scratch/<photo_stem>_entry.md`, run the idempotent catalog upsert tool (`BypassSandbox: true`):
  ```bash
  python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py upsert "<workspace>/<date>/catalog.md" "<workspace>/scratch/<photo_stem>_entry.md"
  ```
  Never call `edit_file` on `<workspace>/<date>/catalog.md` during Phase 3 (and never run `edit_file` on `catalog.md` in parallel with `catalog_tools.py upsert`). If a subagent's `Discrepancy Note` reveals that an earlier `--ref-elev-ft` benchmark was erroneous and leaked into `<workspace>/scratch/<photo_stem>_entry.md`, update `<workspace>/scratch/<photo_stem>_entry.md` via `edit_file` (alongside `<date>/post.md` and `scratch/subagent_prompt_template.txt`) **before** running `catalog_tools.py upsert` so `catalog.md`, `scratch/<photo_stem>_entry.md`, and your displayed response stay 100% identical. Otherwise, do **not** read or edit `<workspace>/scratch/<photo_stem>_entry.md` from the parent agent (unless `upsert` reports non-empty `formatting_issues`), and do **not** run `catalog_tools.py validate` during Phase 3. Because `catalog_tools.py upsert` returns `"normalized_entry"` in its `stdout` JSON, present that `"normalized_entry"` directly to the user **without** calling `read_file` on `scratch/<photo_stem>_entry.md` or performing any independent photo viewing, metadata extraction, or verification in the parent agent.
- **Step C (User Corrections via `send_message` + Re-Upsert)**: If the user questions or corrects the analysis of a completed Photo `$N$`, relay the user's feedback to the existing idle subagent via `send_message(Recipient="<subagent_conversation_id>", Message="...")` (remind it to verify all formatting rules, update `<workspace>/scratch/<photo_stem>_entry.md` via `write_file`, and send a compact completion summary via `send_message` **in the same tool-call turn** without duplicating the full 5–7 KB markdown entry, followed by a 1-sentence final turn with **zero tool calls** after `send_message`) instead of spawning a new subagent or analyzing the photo in the parent. When the subagent replies with its revised analysis, re-run `catalog_tools.py upsert` (which returns the updated `"normalized_entry"` in `stdout`) to update `<workspace>/<date>/catalog.md` in place.
- **Step D (Zero-Overhead Idle Subagent Retention & Canceled/Failed Subagent Retry Rule)**: Because a completed subagent is already idle (`DONE`) and consumes zero compute resources, do **not** call `manage_subagents(Action="kill")` after each photo. Leaving completed subagents idle avoids unnecessary tool calls and allows re-messaging Photo `$N$`'s subagent at any point (even if the user spots a discrepancy after moving on to Photo `$N+1$`). If a running `photo-analysis` subagent is canceled by the user (`explicitly canceled by the user`) or fails to produce `<photo_stem>_entry.md` and the user asks to retry the photo, **never** revive the canceled subagent via `send_message` and **never** call `manage_subagents(Action="list")` (which dumps dozens of historical subagents into context); always verify `scratch/subagent_prompt_template.txt` reflects the latest `SKILL.md` and spawn a brand-new subagent via `invoke_subagent`.

## Phase 4: Synthesis and Final Insertion

Once all photos are analyzed, use the information in `<date>/catalog.md` to
update the narrative in the draft. Identify the right place in the narrative
for each photo and incorporate the details from the photo into the blog post.
You can narrate in the first person if that better fits the overall narrative.

To inspect a compact summary of every cataloged photo's timestamp, elevation,
`Location`, `Suggested Draft Placement` (`<= 220 chars` plus any `[DISCREPANCY: ...]`),
and `Suggested Caption` without writing custom Python scripts or `python -c`
one-liners, run the `placements` subcommand (optionally passing `--out-file "<workspace>/scratch/placements_summary.txt"`):

```bash
python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py placements "<workspace>/<date>"
```

- **Strict Chronological Photo Ordering in `post.md`**: Always place `[PHOTO: <filename>][CAPTION: <caption>]` tags in `<date>/post.md` in strict chronological photo order (`natural_photo_key` / timestamp order). If an earlier photo illustrates a topical beat mentioned in the next paragraph of the initial draft, adjust the paragraph break or weave a brief transition sentence at its true chronological point along the trail rather than placing the photos out of sequence.

Also add a placeholder in the blog post where the photo should be placed and
add the caption from the photo analysis. At the end of the caption, include
the precise GPS coordinates of the photo in parentheses
(e.g. “(36°13’31.9499″ N 118°3’41.1735″ W)”). This placeholder should have the
following format:

```text
[PHOTO: <filename>][CAPTION: <caption>]
```

After inserting all `[PHOTO: <filename>][CAPTION: <caption>]` placeholders into
`<date>/post.md`, run the validation subcommand to verify 1:1 photo coverage,
chronological photo ordering, and editorial compliance before presenting the
synthesized draft:

```bash
python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py validate "<workspace>/<date>"
```

Fix any reported `uncataloged_photos`, `unplaced_photos`, `duplicate_catalog_entries`,
`duplicate_post_placements`, `out_of_order_post_placements`, or `formatting_issues`
(such as forbidden em-dashes `—`, `code_backtick_measurement`, `leaked_image_coordinate`,
`multiple_elevations_in_feature_header`, leaked degree symbols `°` in captions or
natural feature descriptions, or non-standard metadata bullets) until `validate`
reports `"valid": true`.

## Phase 5: Post-Photo Narrative Augmentation (Second Round of Interesting Facts)

Once all photos and their narratives are integrated into `<date>/post.md` and
validated in Phase 4, review the newly added photo landmarks, distant peaks,
geological formations, flora, and wildlife encounters surfaced during Phase 3
that were not yet covered in Phase 2.

- **No Redundant `post.md` Re-Reads Between Phases 4, 5, and 6**: Because `<date>/post.md` was just written in Phase 4 (and edited in Phase 5 Step 2), do **not** call `read_file` on `<date>/post.md` again at the start of Phase 5 or Phase 6 when its current contents are already in your recent context.

1. **Propose a Curated List of Additional Facts**: Present a numbered list of
   6–8 interesting historical, geological, mountaineering, indigenous/etymological,
   or ecological facts tied to specific newly incorporated photo sections (citing
   the photo filename and line/section in `<date>/post.md` where each fact would
   fit). Avoid duplicating facts already integrated in Phase 2.
2. **Incorporate User-Selected Facts & Re-Validate**: Once the user selects
   which facts to include (or asks to incorporate all of them), weave them
   seamlessly into `<date>/post.md` following all editorial constraints (dual
   units, spaced en-dashes ` – `, no em-dashes, no subheadings) and re-run
   `catalog_tools.py validate "<workspace>/<date>"` to confirm `"valid": true`
   with zero formatting issues.

## Phase 6: Comprehensive Editorial Review

After Phase 5 augmentations are integrated into `<date>/post.md`, perform a
thorough end-to-end editorial review of the entire blog post (both narrative
prose and `[CAPTION: ...]` blocks).

1. **Audit for Editorial & Narrative Quality**: Using `<date>/post.md` already in
   your context (without redundantly calling `read_file` if just edited in Phase 5),
   systematically check for:
   - **Factual or Quantitative Inconsistencies**: Conflicting elevations,
     distances, directional bearings, timestamps, or landmark names across the
     header, narrative prose, photo captions, and summary footer (e.g., dual
     benchmarks or slightly different DEM vs. USGS elevations cited for the
     same peak across different paragraphs/captions).
   - **Redundancy & Duplication**: Verbatim or near-verbatim phrasing repeated
     between a paragraph and its immediately following `[CAPTION: ...]`, or the
     same descriptive adjectives, geological explanations, or peak lists echoed
     across consecutive photo transitions.
   - **Stylistic & Pacing Issues**: Formulaic photo-intro sentence structures
     (e.g., repeatedly starting consecutive paragraphs with *"Looking back..."*
     or *"Climbing higher to X ft / Y m..."*), overly dense parenthetical peak
     lists in the prose that disrupt narrative flow, or awkward sentence rhythm.
   - **Abrupt Tone Shifts**: Jarring transitions between personal first-person
     trail storytelling and encyclopedic/textbook-style historical, geological,
     or ecological inserts.
2. **Present Findings & Ask for User Selection**: Present a clear, numbered list
   of specific editorial findings and proposed improvements (referencing exact
   lines in `<date>/post.md`), and ask the user which changes they would like to
   incorporate.
3. **Apply Approved Refinements, Re-Validate & Clean Up `scratch/`**: Once the
   user indicates which refinements to apply, edit `<date>/post.md` (and
   `<date>/catalog.md` if any caption text is updated so the two stay in sync)
   using targeted `edit_file` calls (never rewrite the entire `post.md` with
   `write_file`), re-run `catalog_tools.py validate "<workspace>/<date>"` to
   confirm `"valid": true` with zero formatting issues, and immediately clean up
   `<workspace>/scratch/` (`Remove-Item "<workspace>/scratch/*" -Force`) per
   `## Completion` before presenting your final sign-off.

## Editorial Constraints

- All elevation numbers specified in the blog post MUST be in both feet and
converted meters, in the format “XXX ft / YYY m”. If the converted meters are
missing, please add them.
- All temperatures specified in the blog post MUST be in both Fahrenheit and
Celsius degrees, in the format “XX°F / YY°C”. If the converted Celsius degrees
are missing, please add them.
- Do not introduce any subheadings as you augment the initial blog post.
- Do not use “—”, instead use “–” with a space before and after.
- Do not deviate from the format set forth in the initial draft of each blog
post. Use the narrative as the starting point and incorporate the additional
details.
- Do not drop any material information from the initial draft, but augment it
with the additional information to make it a more engaging read.

## Completion

Conclude by presenting a link to the finalized draft in `<date>/post.md`.

- **Deferred `scratch/` Cleanup**: Clean up temporary files in `<workspace>/scratch/` **only after** all `<date>/post.md` edits, validations, and user reviews are completely finalized (never delete `scratch/` while `post.md` synthesis or follow-up user revisions are still in progress).
- **No Unsolicited Git Commands**: Do not run `git status`, `git diff`, or other `git` commands unless a `.git` repository actually exists in the workspace.
