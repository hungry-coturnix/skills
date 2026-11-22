#!/usr/bin/env python3
"""
Downloads a WordPress blog post (e.g. from pct.ahoh.de) and saves it as Markdown
in <workspace>/<YYYY-MM-DD>/post.md.
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional
from bs4 import BeautifulSoup, NavigableString, Tag

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")


def extract_date_from_url(url: str) -> str:
    match = URL_DATE_RE.search(url)
    if not match:
        raise ValueError(f"Could not extract YYYY/MM/DD date from URL: {url}")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def inline_to_markdown(node) -> str:
    """Recursively converts inline HTML elements (strong, em, a, br) to Markdown."""
    if isinstance(node, NavigableString):
        return str(node).replace("\xa0", " ")
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name == "br":
        return "  \n"

    inner = "".join(inline_to_markdown(child) for child in node.children)
    if name in ("strong", "b"):
        stripped = inner.strip()
        if not stripped:
            return inner
        lead = inner[: len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()) :]
        return f"{lead}**{stripped}**{trail}"
    if name in ("em", "i"):
        stripped = inner.strip()
        if not stripped:
            return inner
        lead = inner[: len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()) :]
        return f"{lead}*{stripped}*{trail}"
    if name == "a":
        href = (node.get("href") or "").strip()
        text = inner.strip()
        if href and text:
            return f"[{text}]({href})"
        return inner
    return inner


def normalize_paragraph(p_tag: Tag) -> str:
    raw = inline_to_markdown(p_tag)
    # Fix cases like "**Elevation Loss**:" -> "**Elevation Loss:**"
    raw = re.sub(r"\*\*([^*]+)\*\*:", r"**\1:**", raw)
    lines = []
    for line in raw.split("\n"):
        has_hard_break = line.endswith("  ")
        cleaned = re.sub(r"[ \t]+", " ", line).strip()
        if has_hard_break and cleaned:
            cleaned += "  "
        lines.append(cleaned)
    if lines and lines[-1].endswith("  "):
        lines[-1] = lines[-1][:-2].rstrip()
    return "\n".join(lines).strip()


def extract_subheadline_meta(main_tag: Tag) -> Optional[str]:
    """
    Extracts 'Jun 20, 2026 — by [Tilman](...) in [Southern California](...)'
    from the post header template part.
    """
    date_div = main_tag.find("div", class_="wp-block-post-date")
    if not date_div:
        return None
    date_text = date_div.get_text(strip=True)

    author_div = main_tag.find("div", class_="wp-block-post-author-name")
    author_part = ""
    if author_div:
        a_tag = author_div.find("a")
        if a_tag and a_tag.get("href"):
            author_part = f"[{a_tag.get_text(strip=True)}]({a_tag['href'].strip()})"
        else:
            author_part = author_div.get_text(strip=True)

    terms_divs = main_tag.find_all("div", class_="wp-block-post-terms")
    term_parts: List[str] = []
    for t_div in terms_divs:
        prefix_span = t_div.find("span", class_="wp-block-post-terms__prefix")
        prefix = prefix_span.get_text(strip=True) if prefix_span else "in"
        links = []
        for a_tag in t_div.find_all("a"):
            href = (a_tag.get("href") or "").strip()
            label = a_tag.get_text(strip=True)
            if href and label:
                links.append(f"[{label}]({href})")
            elif label:
                links.append(label)
        if links:
            term_parts.append(f"{prefix} {', '.join(links)}")

    pieces = [date_text]
    if author_part:
        pieces.append(f"— by {author_part}")
    if term_parts:
        pieces.extend(term_parts)
    return " ".join(pieces)


def parse_post_html(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    main_tag = soup.find("main") or soup

    title_tag = main_tag.find("h1", class_="wp-block-post-title") or main_tag.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled Post"

    meta_line = extract_subheadline_meta(main_tag)

    content_div = main_tag.find("div", class_="entry-content")
    if not content_div:
        raise ValueError("Could not find div.entry-content in HTML.")

    blocks: List[str] = []
    photo_count = 0

    for child in content_div.children:
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        classes = child.get("class") or []

        # Skip scripts, styles, Leaflet map, and email subscription forms
        if name in ("script", "style"):
            continue
        if "leaflet-map" in classes or "emaillist" in classes:
            continue

        if name == "hr":
            if blocks and blocks[-1] != "---":
                blocks.append("---")
            continue

        if name == "p":
            text = normalize_paragraph(child)
            if text:
                blocks.append(text)
            continue

        if name == "figure":
            img = child.find("img")
            if not img:
                continue
            src = (img.get("src") or "").strip()
            if not src:
                continue
            if "elevation-profile" in src.lower():
                continue
            figcaption = child.find("figcaption")
            caption = figcaption.get_text(" ", strip=True).replace("\xa0", " ") if figcaption else ""
            if not caption:
                continue
            blocks.append(f"![{caption}]({src})")
            photo_count += 1
            continue

        if name in ("ul", "ol"):
            items = []
            for idx, li in enumerate(child.find_all("li", recursive=False), 1):
                li_text = normalize_paragraph(li)
                bullet = f"{idx}." if name == "ol" else "*"
                items.append(f"{bullet} {li_text}")
            if items:
                blocks.append("\n".join(items))
            continue

        if name == "div":
            rating_title = child.find("div", class_="pb-star-rating--title")
            if rating_title:
                blocks.append(normalize_paragraph(rating_title))
            continue

    while blocks and blocks[0] == "---":
        blocks.pop(0)
    while blocks and blocks[-1] == "---":
        blocks.pop()

    header_parts = [
        f"<!-- Source: {url} -->",
        f"# {title}",
    ]
    if meta_line:
        header_parts.append(meta_line)

    markdown = "\n\n".join(header_parts + blocks) + "\n"
    return {
        "title": title,
        "meta": meta_line,
        "photo_count": photo_count,
        "markdown": markdown,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download a pct.ahoh.de blog post and save as YYYY-MM-DD/post.md."
    )
    parser.add_argument("urls", nargs="+", help="One or more blog post URLs to download.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root directory where <YYYY-MM-DD>/post.md will be saved (default: current directory).",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    results = []

    for url in args.urls:
        date_str = extract_date_from_url(url)
        html = fetch_html(url)
        parsed = parse_post_html(html, url)

        out_path = workspace / date_str / "post.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        backed_up_to: Optional[str] = None
        if out_path.exists():
            preview_path = out_path.parent / "post_preview.md"
            out_path.replace(preview_path)
            backed_up_to = str(preview_path)

        out_path.write_text(parsed["markdown"], encoding="utf-8", newline="\n")

        entry = {
            "status": "saved",
            "url": url,
            "date": date_str,
            "output_path": str(out_path),
            "title": parsed["title"],
            "photo_count": parsed["photo_count"],
        }
        if backed_up_to:
            entry["renamed_existing_to"] = backed_up_to
        results.append(entry)

    print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
