---
name: geo-tagging
description: >-
  Geo-tags photos from one or more TCX GPS track files using ExifTool. Use this
  skill when asked to geo-tag photos given one or more dates (YYYY-MM-DD) and a
  photo folder name.
---

# Geo-Tagging Photos from TCX Files

Use this procedure to geo-tag a folder of photos using one or more `.tcx` track files with `exiftool`.

## Inputs

1. **Dates**: One or more dates in the format `YYYY-MM-DD` (`<date>`).
2. **Photo Folder Name**: The name of the folder containing the photos (`<folder name>`).

## Procedure

1. **Locate Track Files and Photo Folder**:
   - For each given `<date>`, look for the TCX file at:
     `C:\Users\Tilman\Downloads\<date>.tcx`
   - Look for the photo folder at:
     `D:\Photos\Originals\Public\<folder name>`
   - Verify that all specified TCX files and the target photo folder exist before proceeding.

2. **Run ExifTool**:
   - Construct the `exiftool` command:
     - For each TCX file, append `-geotag "C:\Users\Tilman\Downloads\<date>.tcx"`.
     - At the end of the command, append `"D:\Photos\Originals\Public\<folder name>\*"`.
   - Execute the command and report the number of updated files from the `exiftool` output.

## Example

Given the dates `2026-10-17` and `2026-10-18` and the photo folder `"2026-10-17 PCT Rae Lakes"`:

- **TCX files**:
  - `C:\Users\Tilman\Downloads\2026-10-17.tcx`
  - `C:\Users\Tilman\Downloads\2026-10-18.tcx`
- **Photo folder**:
  - `D:\Photos\Originals\Public\2026-10-17 PCT Rae Lakes`
- **Command**:
  ```powershell
  exiftool -geotag "C:\Users\Tilman\Downloads\2026-10-17.tcx" -geotag "C:\Users\Tilman\Downloads\2026-10-18.tcx" "D:\Photos\Originals\Public\2026-10-17 PCT Rae Lakes\*"
  ```
