#!/usr/bin/env python3
r"""
Catalog Management & Validation CLI (`catalog_tools.py`) for the `hiking-blog-post` skill.

Provides four subcommands:
  1. `upsert <catalog_md_path> <entry_md_path>`:
     Reads a per-photo markdown analysis staging file (`<workspace>/scratch/<photo_stem>_entry.md`),
     validates and normalizes its `### Photo: \`<filename>\`` header and section dividers, and
     idempotently inserts or updates the matching entry in `<date>/catalog.md` in chronological
     photo order without loading `catalog.md` into the LLM context window.

  2. `placements <date_dir>`:
     Parses `<date_dir>/catalog.md` and outputs a compact one-line-per-photo summary of
     `Location`, `Suggested Draft Placement`, and `Suggested Caption` for Phase 4 synthesis:
     `[idx] <photo_name> | Location: <location> | Placement: <placement> | Caption: <caption>`

  3. `status <date_dir>`:
     Reports lightweight cataloging progress (`cataloged_photos`, `last_cataloged_photo`,
     `uncataloged_photos`) for post-compaction state recovery in Phase 3 without inspecting
     `transcript.jsonl` or running full Phase 4 `validate`.

  4. `validate <date_dir>`:
     Scans `<date_dir>/photos/` for `.jpg`/`.jpeg` files, parses `<date_dir>/catalog.md` for
     `### Photo: \`<filename>\`` entries, and parses `<date_dir>/post.md` for
     `[PHOTO: <filename>][CAPTION: ...]` placeholders. Reports a compact JSON summary covering
     photo counts, uncataloged/unplaced photos, duplicate entries, and formatting issues
     (forbidden em-dashes `—`, leaked degree symbols `°` in public descriptions/captions,
     non-canonical metadata bullets, or missing dual units / caption GPS coordinates).
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CANONICAL_METADATA_BULLETS = [
    "Date & Time Taken",
    "GPS Coordinates",
    "Elevation",
    "Location",
]

PHOTO_HEADER_RE = re.compile(
    r"^(#{2,4})\s*(?:Photo(?:\s+\d+)?\s*:\s*`?([^`\n\r]+?)`?|(?:\[?\d+\]?\.?)\s*`?([^`\n\r]+?\.(?:jpg|jpeg|png))`?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

DMS_COORD_RE = re.compile(
    r"\d+\s*°\s*\d+\s*['’′]\s*(?:\d+(?:\.\d+)?\s*[\"”″]?\s*)?[NSEWnsew]?"
)
TEMP_DEG_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*°\s*[FCfc]\b")

# Allow optional first-half imperial range ("9,500 ft to 11,500 ft / ..."),
# optional "~", optional decimals ("300 ft / 91.4 m"), and "–", "-", "to", "and" range connectors
SINGLE_FT_RE = re.compile(
    r"\b(\d{1,2},\d{3}|\d{3,5})\s*ft\b"
    r"(?!\s*(?:[–-]|to|and)\s*~?\s*[\d,]+\s*ft\b)"
    r"(?!\s*/\s*~?\s*[\d,]+(?:\.\d+)?(?:\s*(?:[–-]|to|and)\s*~?\s*[\d,]+(?:\.\d+)?)?\s*m\b)"
)


def natural_photo_key(filename: str) -> List[Tuple[int, Any]]:
    """
    Produces a type-safe natural sort key for photo filenames so that e.g.
    `281A6049.jpg` < `281A6142-Pano.jpg` < `281A6158.jpg`.
    """
    stem = Path(filename).stem
    tokens = re.split(r"(\d+)", stem)
    key: List[Tuple[int, Any]] = []
    for tok in tokens:
        if not tok:
            continue
        if tok.isdigit():
            key.append((0, int(tok)))
        else:
            key.append((1, tok.lower()))
    key.append((1, filename.lower()))
    return key


def strip_outer_fences_and_dividers(raw_text: str) -> str:
    """
    Removes optional outer ```markdown ... ``` code fences and leading/trailing
    `---` horizontal rule lines from a single photo catalog entry.
    """
    text = raw_text.strip()
    fence_match = re.match(
        r"^```(?:markdown|md)?\s*\r?\n(.*?)\r?\n```\s*$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    lines = text.splitlines()
    while lines and re.match(r"^\s*(?:---+|\*\*\*+)\s*$", lines[0]):
        lines.pop(0)
    while lines and re.match(r"^\s*(?:---+|\*\*\*+)\s*$", lines[-1]):
        lines.pop()

    return "\n".join(lines).strip()


def normalize_entry_block(raw_text: str) -> Tuple[str, str]:
    r"""
    Validates that `raw_text` contains a `### Photo: <filename>` header, strips any
    conversational text preceding the header, normalizes the header line to
    `### Photo: \`<filename>\``, and returns `(filename, normalized_entry_text)`.
    """
    cleaned = strip_outer_fences_and_dividers(raw_text)
    match = PHOTO_HEADER_RE.search(cleaned)
    if not match:
        raise ValueError(
            "Entry file does not contain a valid '### Photo: `<filename>`' header."
        )

    raw_filename = (match.group(2) or match.group(3) or "").strip()
    filename = os.path.basename(raw_filename)
    if not filename:
        raise ValueError(f"Could not parse photo filename from header: {match.group(0)!r}")

    body_after_header = cleaned[match.end():]
    normalized_header = f"### Photo: `{filename}`"
    full_entry = strip_outer_fences_and_dividers(normalized_header + body_after_header)
    return filename, full_entry


def parse_catalog_file(catalog_path: Path) -> Tuple[str, List[Tuple[str, str, str]]]:
    """
    Parses an existing `catalog.md` into:
      - `preamble`: markdown header before the first photo section (default `# Photo Catalog`)
      - `entries`: list of `(filename, normalized_entry_text, raw_header_line)`
    """
    if not catalog_path.exists():
        return "# Photo Catalog", []

    content = catalog_path.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return "# Photo Catalog", []

    matches = list(PHOTO_HEADER_RE.finditer(content))
    if not matches:
        preamble = strip_outer_fences_and_dividers(content) or "# Photo Catalog"
        return preamble, []

    preamble_raw = content[: matches[0].start()]
    preamble = strip_outer_fences_and_dividers(preamble_raw) or "# Photo Catalog"

    entries: List[Tuple[str, str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if (i + 1) < len(matches) else len(content)
        raw_chunk = content[start:end]
        raw_header_line = m.group(0).strip()
        fn, norm_chunk = normalize_entry_block(raw_chunk)
        entries.append((fn, norm_chunk, raw_header_line))

    return preamble, entries


def find_leaked_degree_symbols(text: str) -> List[str]:
    """
    Detects degree symbols (`°`) that are NOT part of DMS GPS coordinates
    (e.g. `36°13'31.95" N`) and NOT part of temperature values (`45°F / 7°C`).
    Returns a list of short context snippets around any leaked `°`.
    """
    if "°" not in text:
        return []
    scrubbed = DMS_COORD_RE.sub("<DMS>", text)
    scrubbed = TEMP_DEG_RE.sub("<TEMP>", scrubbed)
    if "°" not in scrubbed:
        return []
    snippets = []
    for m in re.finditer(r".{0,30}°.{0,30}", scrubbed):
        snippet = re.sub(r"\s+", " ", m.group(0)).strip()
        snippets.append(snippet)
    return snippets


def inspect_entry_formatting(filename: str, entry_text: str, raw_header_line: Optional[str] = None) -> List[Dict[str, str]]:
    r"""
    Checks a single catalog entry for:
      - Non-canonical `### Photo: \`<filename>\`` header
      - Forbidden em-dashes (`—`)
      - Extra or missing metadata bullets beyond the 4 canonical bullets
      - Single-unit `ft` elevations missing `/ YYY m` across the `- **Elevation**:`
        metadata bullet, `### Identified Natural Features`, `### Identified Fauna & Flora`,
        and `### Suggested Caption` (excluding `### Suggested Draft Placement`)
      - Leaked degree symbols (`°`) in `Identified Natural Features`, `Identified Fauna & Flora`,
        or `Suggested Caption`
      - Missing trailing GPS coordinates in `Suggested Caption`
    """
    issues: List[Dict[str, str]] = []
    expected_header = f"### Photo: `{filename}`"
    if raw_header_line is not None and raw_header_line.strip() != expected_header:
        issues.append({
            "source": f"catalog.md ({filename})",
            "type": "non_canonical_photo_header",
            "detail": f"Header is '{raw_header_line.strip()}', expected '{expected_header}'.",
        })

    if "—" in entry_text:
        issues.append({
            "source": f"catalog.md ({filename})",
            "type": "forbidden_em_dash",
            "detail": "Contains forbidden em-dash ('—'); use spaced en-dash (' – ') instead.",
        })

    # Split entry into subsections by `### `
    parts = re.split(r"(?m)^###\s+", entry_text)
    # parts[0] is empty (before `### Photo:`), parts[1] is `Photo: ...` metadata block
    if len(parts) >= 2:
        meta_block = parts[1]
        bullet_labels = re.findall(r"(?m)^\s*-\s*\*\*([^*]+?)\*\*\s*:", meta_block)
        extra_bullets = [b for b in bullet_labels if b not in CANONICAL_METADATA_BULLETS]
        missing_bullets = [b for b in CANONICAL_METADATA_BULLETS if b not in bullet_labels]
        if extra_bullets:
            issues.append({
                "source": f"catalog.md ({filename})",
                "type": "extra_metadata_bullets",
                "detail": (
                    f"Found non-standard metadata bullet(s) {extra_bullets}. "
                    f"Only the 4 canonical bullets {CANONICAL_METADATA_BULLETS} are permitted."
                ),
            })
        if missing_bullets:
            issues.append({
                "source": f"catalog.md ({filename})",
                "type": "missing_metadata_bullets",
                "detail": f"Missing required metadata bullet(s): {missing_bullets}.",
            })

        # Enforce dual-unit elevation check on the `- **Elevation**:` metadata bullet
        for m_elev in re.finditer(r"(?m)^\s*-\s*\*\*Elevation\*\*\s*:(.*)$", meta_block):
            elev_val = m_elev.group(1)
            for m_ft in SINGLE_FT_RE.finditer(elev_val):
                snippet = re.sub(
                    r"\s+",
                    " ",
                    elev_val[max(0, m_ft.start() - 20): min(len(elev_val), m_ft.end() + 25)],
                ).strip()
                issues.append({
                    "source": f"catalog.md ({filename} -> Elevation)",
                    "type": "missing_dual_elevation_units",
                    "detail": f"Elevation in feet missing '/ YYY m' conversion near: '{snippet}'",
                })

    for part in parts[2:]:
        lines = part.splitlines()
        if not lines:
            continue
        sec_title = lines[0].strip()
        sec_body = "\n".join(lines[1:]).strip()
        sec_lower = sec_title.lower()

        is_draft_placement = "suggested draft placement" in sec_lower
        is_checked_section = not is_draft_placement and (
            "identified natural features" in sec_lower
            or "identified fauna" in sec_lower
            or "suggested caption" in sec_lower
        )

        if is_checked_section:
            for m_ft in SINGLE_FT_RE.finditer(sec_body):
                snippet = re.sub(
                    r"\s+",
                    " ",
                    sec_body[max(0, m_ft.start() - 20): min(len(sec_body), m_ft.end() + 25)],
                ).strip()
                issues.append({
                    "source": f"catalog.md ({filename} -> {sec_title})",
                    "type": "missing_dual_elevation_units",
                    "detail": f"Elevation in feet missing '/ YYY m' conversion near: '{snippet}'",
                })

        if "identified natural features" in sec_lower or "identified fauna" in sec_lower or "suggested caption" in sec_lower:
            leaked = find_leaked_degree_symbols(sec_body)
            if leaked:
                issues.append({
                    "source": f"catalog.md ({filename} -> {sec_title})",
                    "type": "leaked_degree_symbol",
                    "detail": (
                        f"Leaked degree symbol (°) in '{sec_title}' "
                        f"(use cardinal directions and distances only): {leaked}"
                    ),
                })

        if "suggested caption" in sec_lower and sec_body:
            if not re.search(r"\([^()]*\d+\s*°\s*\d+\s*['’′][^()]*\)\s*\.?\s*$", sec_body):
                issues.append({
                    "source": f"catalog.md ({filename} -> Suggested Caption)",
                    "type": "missing_caption_gps_coords",
                    "detail": "Suggested Caption does not end with DMS GPS coordinates in parentheses.",
                })

    return issues


def extract_entry_summary_fields(entry_text: str) -> Dict[str, str]:
    """
    Extracts single-line `location`, `placement`, and `caption` fields from a
    normalized catalog entry block for Phase 4 synthesis (`placements`).
    """
    location = ""
    placement = ""
    caption = ""

    parts = re.split(r"(?m)^###\s+", entry_text)
    if len(parts) >= 2:
        meta_block = parts[1]
        m_loc = re.search(r"(?m)^\s*-\s*\*\*Location\*\*\s*:\s*(.+)$", meta_block)
        if m_loc:
            location = re.sub(r"\s+", " ", m_loc.group(1)).strip()

    for part in parts[2:]:
        lines = part.splitlines()
        if not lines:
            continue
        sec_title = lines[0].strip().lower()
        sec_body = re.sub(r"\s+", " ", "\n".join(lines[1:])).strip()
        if "suggested draft placement" in sec_title:
            placement = sec_body
        elif "suggested caption" in sec_title:
            caption = sec_body

    return {
        "location": location,
        "placement": placement,
        "caption": caption,
    }


def _resolve_date_dir_and_catalog(path_arg: str) -> Tuple[Path, Path]:
    p = Path(path_arg)
    if p.is_file() or p.name.lower().endswith(".md"):
        return p.parent, p
    return p, p / "catalog.md"


def cmd_upsert(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog_md_path)
    entry_path = Path(args.entry_md_path)

    if not entry_path.exists():
        print(
            json.dumps({
                "status": "error",
                "error": f"Entry file not found: {entry_path}",
            }),
            file=sys.stderr,
        )
        sys.exit(1)

    raw_entry = entry_path.read_text(encoding="utf-8", errors="replace")
    try:
        target_fn, normalized_entry = normalize_entry_block(raw_entry)
    except ValueError as exc:
        print(
            json.dumps({
                "status": "error",
                "error": str(exc),
                "entry_path": str(entry_path),
            }),
            file=sys.stderr,
        )
        sys.exit(1)

    preamble, raw_entries = parse_catalog_file(catalog_path)

    # Deduplicate existing entries while preserving order
    dedup_entries: List[Tuple[str, str]] = []
    seen_indices: Dict[str, int] = {}
    for fn, text, _ in raw_entries:
        if fn in seen_indices:
            dedup_entries[seen_indices[fn]] = (fn, text)
        else:
            seen_indices[fn] = len(dedup_entries)
            dedup_entries.append((fn, text))

    if target_fn in seen_indices:
        idx = seen_indices[target_fn]
        dedup_entries[idx] = (target_fn, normalized_entry)
        status = "updated"
    else:
        status = "inserted"
        target_key = natural_photo_key(target_fn)
        insert_idx = len(dedup_entries)
        for i, (existing_fn, _) in enumerate(dedup_entries):
            if natural_photo_key(existing_fn) > target_key:
                insert_idx = i
                break
        dedup_entries.insert(insert_idx, (target_fn, normalized_entry))

    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    body_str = "\n\n---\n\n".join(text for _, text in dedup_entries)
    final_catalog_text = f"{preamble.strip()}\n\n{body_str}\n"
    catalog_path.write_text(final_catalog_text, encoding="utf-8", newline="\n")

    # Also normalize the staging file in place so it always has the canonical header and LF line endings
    entry_path.write_text(normalized_entry + "\n", encoding="utf-8", newline="\n")

    entry_issues = inspect_entry_formatting(target_fn, normalized_entry)
    result: Dict[str, Any] = {
        "status": status,
        "photo": target_fn,
        "catalog_path": str(catalog_path),
        "total_cataloged_photos": len(dedup_entries),
        "next_step_reminder": (
            "Present the entry to the user now. Do NOT read/edit the staging file "
            "or run 'validate' until all photos are cataloged and placed in post.md (Phase 4)."
        ),
        "normalized_entry": normalized_entry,
    }
    if entry_issues:
        result["formatting_issues"] = entry_issues

    print(json.dumps(result, ensure_ascii=False))


def cmd_placements(args: argparse.Namespace) -> None:
    _, catalog_path = _resolve_date_dir_and_catalog(args.date_dir)
    if not catalog_path.exists():
        print(
            json.dumps({
                "status": "error",
                "error": f"Catalog file not found at {catalog_path}",
            }),
            file=sys.stderr,
        )
        sys.exit(1)

    _, parsed_entries = parse_catalog_file(catalog_path)
    items: List[Dict[str, Any]] = []
    lines_out: List[str] = []
    for idx, (fn, norm_text, _) in enumerate(parsed_entries, 1):
        fields = extract_entry_summary_fields(norm_text)
        loc = fields["location"]
        place = fields["placement"]
        cap = fields["caption"]
        items.append({
            "index": idx,
            "photo": fn,
            "location": loc,
            "placement": place,
            "caption": cap,
        })
        lines_out.append(
            f"[{idx}] {fn} | Location: {loc} | Placement: {place} | Caption: {cap}"
        )

    if getattr(args, "json", False):
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines_out))


def cmd_status(args: argparse.Namespace) -> None:
    date_dir, catalog_path = _resolve_date_dir_and_catalog(args.date_dir)
    photos_dir = date_dir / "photos"
    post_path = date_dir / "post.md"

    folder_photos: List[str] = []
    if photos_dir.exists() and photos_dir.is_dir():
        for p in photos_dir.iterdir():
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"):
                folder_photos.append(p.name)
        folder_photos.sort(key=natural_photo_key)

    cataloged_photos: List[str] = []
    if catalog_path.exists():
        _, parsed_entries = parse_catalog_file(catalog_path)
        cataloged_photos = [fn for fn, _, _ in parsed_entries]

    cataloged_set = set(cataloged_photos)
    uncataloged_photos = [fn for fn in folder_photos if fn not in cataloged_set]

    placed_photos: List[str] = []
    if post_path.exists():
        post_text = post_path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\[PHOTO:\s*`?([^\]`\s]+?)`?\s*\]", post_text):
            placed_photos.append(os.path.basename(m.group(1).strip()))

    status_payload = {
        "date_dir": str(date_dir),
        "catalog_path": str(catalog_path),
        "total_photos_in_folder": len(folder_photos),
        "cataloged_count": len(cataloged_photos),
        "last_cataloged_photo": cataloged_photos[-1] if cataloged_photos else None,
        "cataloged_photos": cataloged_photos,
        "uncataloged_photos": uncataloged_photos,
        "placed_in_post_count": len(set(placed_photos)),
    }
    print(json.dumps(status_payload, ensure_ascii=False, indent=2))


def cmd_validate(args: argparse.Namespace) -> None:
    date_dir = Path(args.date_dir)
    photos_dir = date_dir / "photos"
    catalog_path = date_dir / "catalog.md"
    post_path = date_dir / "post.md"

    # 1. Scan photos directory
    folder_photos: List[str] = []
    if photos_dir.exists() and photos_dir.is_dir():
        for p in photos_dir.iterdir():
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"):
                folder_photos.append(p.name)
        folder_photos.sort(key=natural_photo_key)

    # 2. Parse catalog.md
    formatting_issues: List[Dict[str, str]] = []
    catalog_photo_occurrences: List[str] = []
    if catalog_path.exists():
        catalog_raw = catalog_path.read_text(encoding="utf-8", errors="replace")
        preamble, parsed_entries = parse_catalog_file(catalog_path)
        if "—" in preamble:
            formatting_issues.append({
                "source": "catalog.md (preamble)",
                "type": "forbidden_em_dash",
                "detail": "Catalog preamble contains forbidden em-dash ('—').",
            })
        for fn, norm_text, raw_header in parsed_entries:
            catalog_photo_occurrences.append(fn)
            formatting_issues.extend(inspect_entry_formatting(fn, norm_text, raw_header))
    else:
        formatting_issues.append({
            "source": "catalog.md",
            "type": "missing_file",
            "detail": f"Catalog file not found at {catalog_path}",
        })

    catalog_counts = Counter(catalog_photo_occurrences)
    duplicate_catalog_entries = [fn for fn, cnt in catalog_counts.items() if cnt > 1]
    cataloged_set = set(catalog_photo_occurrences)

    # 3. Parse post.md
    placed_photo_occurrences: List[str] = []
    if post_path.exists():
        post_text = post_path.read_text(encoding="utf-8", errors="replace")

        # Check for forbidden em-dashes in post.md
        for line_num, line in enumerate(post_text.splitlines(), 1):
            if "—" in line:
                snippets = [s.strip() for s in re.findall(r".{0,25}—.{0,25}", line)]
                formatting_issues.append({
                    "source": f"post.md (line {line_num})",
                    "type": "forbidden_em_dash",
                    "detail": f"Contains forbidden em-dash ('—'); use ' – ' instead: {snippets}",
                })

        # Match [PHOTO: <filename>][CAPTION: <caption>]
        full_placeholder_re = re.compile(
            r"\[PHOTO:\s*`?([^\]`\s]+?)`?\s*\]\s*\[CAPTION:\s*(.*?)\]",
            re.DOTALL,
        )
        matched_spans: List[Tuple[int, int]] = []
        for m in full_placeholder_re.finditer(post_text):
            fn = os.path.basename(m.group(1).strip())
            caption = m.group(2).strip()
            placed_photo_occurrences.append(fn)
            matched_spans.append((m.start(), m.end()))

            leaked = find_leaked_degree_symbols(caption)
            if leaked:
                formatting_issues.append({
                    "source": f"post.md ([PHOTO: {fn}] caption)",
                    "type": "leaked_degree_symbol",
                    "detail": f"Leaked degree symbol (°) in caption: {leaked}",
                })

            if not re.search(r"\([^()]*\d+\s*°\s*\d+\s*['’′][^()]*\)\s*\.?\s*$", caption):
                formatting_issues.append({
                    "source": f"post.md ([PHOTO: {fn}] caption)",
                    "type": "missing_caption_gps_coords",
                    "detail": "Photo caption in post.md does not end with DMS GPS coordinates in parentheses.",
                })

        # Check for any bare [PHOTO: <filename>] missing [CAPTION: ...]
        for m in re.finditer(r"\[PHOTO:\s*`?([^\]`\s]+?)`?\s*\]", post_text):
            if not any(start <= m.start() < end for start, end in matched_spans):
                fn = os.path.basename(m.group(1).strip())
                placed_photo_occurrences.append(fn)
                formatting_issues.append({
                    "source": f"post.md ([PHOTO: {fn}])",
                    "type": "missing_caption_block",
                    "detail": f"[PHOTO: {fn}] placeholder is not followed by [CAPTION: ...].",
                })

        # Check for single-unit elevations (e.g. `11,425 ft` not followed by `/ ... m`)
        for m in SINGLE_FT_RE.finditer(post_text):
            snippet = re.sub(r"\s+", " ", post_text[max(0, m.start() - 20): min(len(post_text), m.end() + 25)]).strip()
            formatting_issues.append({
                "source": "post.md",
                "type": "missing_dual_elevation_units",
                "detail": f"Elevation in feet missing '/ YYY m' conversion near: '{snippet}'",
            })

        # Check for single-unit temperatures (e.g. `45°F` not followed by `/ ...°C`)
        for m in re.finditer(r"[-+]?\d+(?:\.\d+)?\s*°F\b(?!\s*/\s*[-+]?\d+(?:\.\d+)?\s*°C\b)", post_text):
            snippet = re.sub(r"\s+", " ", post_text[max(0, m.start() - 20): min(len(post_text), m.end() + 25)]).strip()
            formatting_issues.append({
                "source": "post.md",
                "type": "missing_dual_temperature_units",
                "detail": f"Temperature in °F missing '/ YY°C' conversion near: '{snippet}'",
            })
    else:
        formatting_issues.append({
            "source": "post.md",
            "type": "missing_file",
            "detail": f"Blog post draft not found at {post_path}",
        })

    placed_counts = Counter(placed_photo_occurrences)
    duplicate_post_placements = [fn for fn, cnt in placed_counts.items() if cnt > 1]
    placed_set = set(placed_photo_occurrences)

    all_expected_photos = sorted(
        set(folder_photos) | cataloged_set,
        key=natural_photo_key,
    )
    uncataloged_photos = [fn for fn in folder_photos if fn not in cataloged_set]
    unplaced_photos = [fn for fn in all_expected_photos if fn not in placed_set]

    is_valid = (
        len(uncataloged_photos) == 0
        and len(unplaced_photos) == 0
        and len(duplicate_catalog_entries) == 0
        and len(duplicate_post_placements) == 0
        and len(formatting_issues) == 0
    )

    summary = {
        "date_dir": str(date_dir),
        "valid": is_valid,
        "total_photos_in_folder": len(folder_photos),
        "cataloged_count": len(cataloged_set),
        "placed_in_post_count": len(placed_set),
        "uncataloged_photos": uncataloged_photos,
        "unplaced_photos": unplaced_photos,
        "duplicate_catalog_entries": duplicate_catalog_entries,
        "duplicate_post_placements": duplicate_post_placements,
        "formatting_issues": formatting_issues,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotent catalog.md upsert and post/catalog validation utility for hiking-blog-post."
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_upsert = subparsers.add_parser(
        "upsert",
        help="Insert or update a single photo analysis entry in catalog.md.",
    )
    p_upsert.add_argument("catalog_md_path", help="Path to <date>/catalog.md")
    p_upsert.add_argument("entry_md_path", help="Path to <workspace>/scratch/<photo_stem>_entry.md")

    p_place = subparsers.add_parser(
        "placements",
        help="Output a compact summary of Location, Suggested Draft Placement, and Suggested Caption for Phase 4 synthesis.",
    )
    p_place.add_argument("date_dir", help="Path to <date> directory (or <date>/catalog.md)")
    p_place.add_argument("--json", action="store_true", help="Output structured JSON instead of compact lines")

    p_status = subparsers.add_parser(
        "status",
        help="Report lightweight cataloging progress for post-compaction state recovery in Phase 3.",
    )
    p_status.add_argument("date_dir", help="Path to <date> directory (or <date>/catalog.md)")

    p_val = subparsers.add_parser(
        "validate",
        help="Validate 1:1 photo coverage and formatting across <date>/photos, catalog.md, and post.md.",
    )
    p_val.add_argument("date_dir", help="Path to <date> directory (containing photos/, catalog.md, post.md)")

    args = parser.parse_args()
    if args.subcommand == "upsert":
        cmd_upsert(args)
    elif args.subcommand == "placements":
        cmd_placements(args)
    elif args.subcommand == "status":
        cmd_status(args)
    elif args.subcommand == "validate":
        cmd_validate(args)


if __name__ == "__main__":
    main()
