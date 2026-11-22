---
name: download-blog-post
description: >-
  Downloads a published blog post from pct.ahoh.de (or similar WordPress post
  URL containing YYYY/MM/DD) and saves it as formatted Markdown in
  YYYY-MM-DD/post.md. Use this skill whenever asked to download or import a
  blog post URL into the workspace.
---

# Download Blog Post to Markdown

Use this skill to download one or more published blog posts from `pct.ahoh.de` and save them as clean Markdown files in `<workspace>/<YYYY-MM-DD>/post.md`.

## Procedure

1. **Run the Download Script**:
   - **Mandatory Unsandboxed Execution**: Always pass `BypassSandbox: true` on the **very first attempt** when running [`download_post.py`](./scripts/download_post.py) (never attempt to run it in the sandbox first, as network access is required to fetch the URL).
   ```powershell
   python C:\Users\Tilman\.gemini\config\skills\download-blog-post\scripts\download_post.py "<url>"
   ```
   - Multiple URLs can be passed in a single invocation.
   - By default, the script extracts `YYYY/MM/DD` from each URL and writes to `<cwd>/YYYY-MM-DD/post.md`. Pass `--workspace "<path>"` if the target workspace differs from the current working directory.
   - If `<YYYY-MM-DD>/post.md` already exists, the script automatically renames the existing file to `<YYYY-MM-DD>/post_preview.md` before writing the downloaded `post.md`.

2. **Verify Output**:
   - Check the JSON summary printed to `stdout` (`status`, `date`, `output_path`, `title`, `photo_count`, and optional `renamed_existing_to`).
   - Present a clickable link to the generated `YYYY-MM-DD/post.md` file.
