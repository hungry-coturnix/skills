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
format YYYY-MM-DD (which we will refer to as <date> in the instructions below)
and the initial draft of the blog post. Save the draft to <date>/post.md. You
can make any subsequent changes to the draft inline in this file.

Please double check the draft for factual correctness and flag anything that
may be inaccurate. Please correct any language mistakes (e.g. spelling,
grammar, etc.). List all changes you have made for reference.

## Phase 2: Narrative Augmentation

Suggest any additional facts that we might want to incorporate into the post,
for example facts about the history, geography, geology, etc. for natural
features, or details about the species, evolution, habitat, behavior, etc. for
references to fauna or flora. Scan existing <YYYY-MM-DD>/post.md folders in
the workspace to identify relevant callbacks to past hikes (avoiding
duplication while highlighting continuity).

## Phase 3: Photo Processing

After the initial draft, you will get a set of photos, one by one, that were
taken during the hike. You will only be provided with a filename. All photos
are located in the directory `<date>/photos`:
1. **Strict Delegation Boundary**: Do **not** view photos, run `extract_metadata.py` / `crop_photo.py`, or perform preliminary feature identification in the main orchestrator agent. Immediately spawn a subagent to run the `photo-analysis` skill on the image.
2. **Single-Writer `catalog.md` Rule**: Instruct each subagent to return its completed markdown analysis block directly in its final response without reading or editing `<date>/catalog.md`. Once the subagent returns its analysis, the main orchestrator agent appends the resulting analysis to `<date>/catalog.md`.
3. **Required Subagent Context**: In every subagent prompt, explicitly include:
   - The exact photo path (`<workspace>/<date>/photos/<filename>`) and the exact blog draft path (`<workspace>/<date>/post.md`).
   - Benchmark reference elevations from `<date>/post.md` formatted as `--ref-elev-ft "Label:ElevFt"` flags for `extract_metadata.py` (e.g., `--ref-elev-ft "Trailhead:9936" --ref-elev-ft "Cottonwood Pass:11160"`).
   - A reminder to use `geo_tools.py horizon-skyline` and `render-topo` for horizon/peak verification and to prefix any temporary files in `scratch/` with the photo stem (`scratch/<photo_stem>_*`) so concurrent subagents never overwrite each other's files.

## Phase 4: Synthesis and Final Insertion

Once all photos are analyzed, use the information in <date>/catalog.md to
update the narrative in the draft. Identify the right place in the narrative
for each photo and incorporate the details from the photo into the blog post.
You can narrate in the first person if that better fits the overall narrative.

Also add a placeholder in the blog post where the photo should be placed and
add the caption from the photo analysis. At the end of the caption, include
the precise GPS coordinates of the photo in parentheses
(e.g. “(36°13’31.9499″ N 118°3’41.1735″ W)”). This placeholder should have the
following format:

[PHOTO: <filename>][CAPTION: <caption>]

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

Conclude by presenting a link to the finalized draft in <date>/post.md.
