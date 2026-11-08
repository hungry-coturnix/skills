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

## Phase 3: Photo Processing

After the initial draft, you will get a set of photos, one by one, that were
taken during the hike. You will only be provided with a filename. All photos
are located in the directory `<date>/photos`.

### 1. Strict Delegation Boundary & Anti-Amnesia Guardrails
- **No Parent Photo Analysis**: Do **not** view photos (`read_file`), run `extract_metadata.py` / `crop_photo.py` / `geo_tools.py`, run `Test-Path` checks, or perform independent geographic/flora verification in the main orchestrator agent. Immediately delegate each photo to a `self` subagent running the `photo-analysis` skill, and let the user verify the subagent's output.
- **Persist the Hike-Specific Subagent Prompt Template & 4-Step Lifecycle Header to Disk**: At the start of Phase 3 (before spawning the first photo subagent), save your hike-specific subagent prompt template (with the hike's `<workspace>`, `<date>`, and pre-computed `--ref-elev-ft "Label:ElevFt"` benchmark flags filled in, plus a comment header summarizing the Parent 4-Step Lifecycle rules below) to `<workspace>/scratch/subagent_prompt_template.txt` using `write_file`.
- **Post-Compaction Recovery Rules**: If context compaction occurs during Phase 3, immediately re-read **both** `<workspace>/scratch/subagent_prompt_template.txt` **AND** `C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\SKILL.md` (lines 50–100) upon waking so your subagent prompts and Parent 4-Step Lifecycle rules never degrade.
- **No Per-Photo `validate` Calls in Phase 3**: Do **NOT** run `catalog_tools.py validate` after individual photos in Phase 3—`upsert` already runs entry formatting checks on each photo and returns `formatting_issues` and `normalized_entry`. Run `validate` **ONLY once** in Phase 4 after inserting all `[PHOTO: ...]` tags into `post.md`.
- **No Transcript Scavenging or Inline `python -c` Scripts**: Never read or grep `.system_generated/logs/transcript.jsonl` or `transcript_full.jsonl` after context compaction, and never run inline `python -c` one-liners to parse or update `<date>/catalog.md`. To check whether the current photo was already upserted before a mid-turn context compaction, run `python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py status "<workspace>/<date>"`—never inspect `transcript.jsonl`.

### 2. Canonical Subagent Prompt Template
Pass this exact prompt structure (filling in `<workspace>`, `<date>`, `<filename>`, `<photo_stem>`, and the hike's `--ref-elev-ft` flags) to `invoke_subagent`:

```text
Analyze the hiking photo `<workspace>/<date>/photos/<filename>` using the `photo-analysis` skill:

1. MANDATORY FIRST STEP: Read `C:\Users\Tilman\.gemini\config\skills\photo-analysis\SKILL.md` in full and follow all instructions, geometric/solar verification guardrails, and output formatting rules before running any other tools.
2. Stage 1 (Unbiased Geometric Discovery — Before Reading `post.md`):
   - Run `extract_metadata.py` with the hike's reference elevation benchmarks:
     `python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\extract_metadata.py "<workspace>/<date>/photos/<filename>" --ref-elev-ft "<Label1>:<ElevFt1>" --ref-elev-ft "<Label2>:<ElevFt2>"`
   - View the photo (`read_file`) and run `geo_tools.py` (`sun-position`, `nearby-features`, `horizon-skyline --out-pano "<workspace>/scratch/<photo_stem>_pano.png" --out-json "<workspace>/scratch/<photo_stem>_skyline.json"`, and `render-topo --out "<workspace>/scratch/<photo_stem>_topo.png"`) and `crop_photo.py` to establish the true physical camera orientation and visible features.
   - Prefix ALL temporary files saved in `<workspace>/scratch/` with `<photo_stem>_`.
3. Stage 2 (Narrative Cross-Check & Draft Placement):
   - After establishing your geometric orientation in Stage 1, read `<workspace>/<date>/post.md` to cross-check trail/junction context, test any landmarks mentioned near this segment, and determine the exact `Suggested Draft Placement`.
4. SINGLE-WRITER RULE: Do NOT read, grep, or modify `<workspace>/<date>/catalog.md`. Format your output strictly using the 4-bullet `## Output Format` template in `photo-analysis/SKILL.md`. Write your complete markdown entry (`### Photo: \`<filename>\`` through `### Suggested Draft Placement`) to `<workspace>/scratch/<photo_stem>_entry.md` using `write_file` (`Overwrite: true`), send the file path and complete markdown entry **once** to the parent agent via `send_message`, and end your final response with a 1-sentence confirmation without repeating the markdown block.
```

### 3. 4-Step Per-Photo Lifecycle & Subagent Reuse Protocol
For each photo `$N$` provided by the user, follow this exact 4-step lifecycle:

- **Step A (Invoke Subagent)**: Spawn the `photo-analysis` subagent (`TypeName="self"`) for Photo `$N$` using the canonical prompt template above.
- **Step B (Immediate Disk Upsert & User Presentation)**: As soon as the subagent completes and writes `<workspace>/scratch/<photo_stem>_entry.md`, run the idempotent catalog upsert tool:
  ```bash
  python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py upsert "<workspace>/<date>/catalog.md" "<workspace>/scratch/<photo_stem>_entry.md"
  ```
  Do **not** read or edit `<workspace>/scratch/<photo_stem>_entry.md` from the parent agent (unless `upsert` reports non-empty `formatting_issues`), and do **not** run `catalog_tools.py validate` during Phase 3. Present the normalized entry directly to the user **without** performing any independent photo viewing, metadata extraction, or verification in the parent agent.
- **Step C (User Corrections via `send_message` + Re-Upsert)**: If the user questions or corrects the analysis of Photo `$N$`, relay the user's feedback to the existing idle subagent via `send_message(Recipient="<subagent_conversation_id>", Message="...")` (remind it to update `<workspace>/scratch/<photo_stem>_entry.md`, send the revised markdown entry **once** via `send_message`, and end its final response with a 1-sentence confirmation) instead of spawning a new subagent or analyzing the photo in the parent. When the subagent replies with its revised analysis, re-run `catalog_tools.py upsert` to update `<workspace>/<date>/catalog.md` in place.
- **Step D (Zero-Overhead Idle Subagent Retention)**: Because a completed subagent is already idle (`DONE`) and consumes zero compute resources, do **not** call `manage_subagents(Action="kill")` after each photo. Leaving completed subagents idle avoids unnecessary tool calls and allows re-messaging Photo `$N$`'s subagent at any point (even if the user spots a discrepancy after moving on to Photo `$N+1$`).

## Phase 4: Synthesis and Final Insertion

Once all photos are analyzed, use the information in `<date>/catalog.md` to
update the narrative in the draft. Identify the right place in the narrative
for each photo and incorporate the details from the photo into the blog post.
You can narrate in the first person if that better fits the overall narrative.

To inspect a compact summary of every cataloged photo's `Location`,
`Suggested Draft Placement`, and `Suggested Caption` without writing custom
Python scripts or `python -c` one-liners, run the `placements` subcommand:

```bash
python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py placements "<workspace>/<date>"
```

Also add a placeholder in the blog post where the photo should be placed and
add the caption from the photo analysis. At the end of the caption, include
the precise GPS coordinates of the photo in parentheses
(e.g. “(36°13’31.9499″ N 118°3’41.1735″ W)”). This placeholder should have the
following format:

```text
[PHOTO: <filename>][CAPTION: <caption>]
```

After inserting all `[PHOTO: <filename>][CAPTION: <caption>]` placeholders into
`<date>/post.md`, run the validation subcommand to verify 1:1 photo coverage
and editorial compliance before presenting the final draft:

```bash
python C:\Users\Tilman\.gemini\config\skills\hiking-blog-post\scripts\catalog_tools.py validate "<workspace>/<date>"
```

Fix any reported `uncataloged_photos`, `unplaced_photos`, `duplicate_catalog_entries`,
or `formatting_issues` (such as forbidden em-dashes `—`, leaked degree symbols `°`
in captions or natural feature descriptions, or non-standard metadata bullets)
until `validate` reports `"valid": true`.

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
