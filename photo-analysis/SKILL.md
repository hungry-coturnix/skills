---
name: photo-analysis
description: >-
  Extracts structured visual details, setting, and subjects from 
  hiking and travel photos for integrating into blog posts. Use
  whenever analyzing photos to match with blog draft sections.
---

# Hiking Photo Analysis

Use this procedure to analyze a hiking photo and produce a concise, structured visual summary without bloating the conversation context. Augment this summary with verified geographic, botanical, and geological details relevant to what is visible in the photo.

## Helper Scripts

This skill includes pre-packaged helper utilities in `scripts/`:
- **`scripts/extract_metadata.py`**: Runs ExifTool to extract timestamp, formatted DMS GPS coordinates, decimal coordinates, camera/lens model, focal length (`focal_length_35mm`), horizontal field-of-view (`estimated_single_frame_hfov_deg`), image dimensions, chronologically preceding/succeeding photo walking vectors (`prev_photo`, `next_photo`), and queries the USGS 3DEP Elevation Point Query Service (EPQS) with retry for official ground elevation in one shot:
  ```bash
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\extract_metadata.py "<photo_path>" --ref-elev-ft "Trailhead:5267" --ref-elev-ft "High Point:7990"
  ```
  Do **not** run `exiftool.exe` separately after running `extract_metadata.py`, as all camera, lens, dimension, and neighbor travel-vector fields are already included.
  *Fallback command*: Only if `extract_metadata.py` cannot be run, extract display DMS, calculation decimals, and altitude in one call:
  ```bash
  exiftool.exe -d "%Y-%m-%d %H:%M:%S" -DateTimeOriginal -GPSPosition -c "%d°%d'%.2f\"" -GPSLatitude# -GPSLongitude# -GPSAltitude# -FocalLength# -FocalLengthIn35mmFormat# "<photo_path>"
  ```
- **`scripts/crop_photo.py`**: Crops high-resolution images to inspect fine details (botanical features, distant peaks, trail junctions, lake shapes) without downsampling artifacts:
  ```bash
  # Predefined regions: center, top-left, top-right, bottom-left, bottom-right, left, right
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --region center
  # Horizontal panorama slices (e.g. slice 2 of 4)
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --slice 2 4
  # Bounding box in fractions 0.0 - 1.0 (X1 Y1 X2 Y2)
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --box 0.2 0.3 0.6 0.7
  ```
- **`scripts/geo_tools.py`**: Unified geographic, 3DEP DEM horizon skyline, line-of-sight, topo map rendering, and solar analysis utility to eliminate spatial boilerplate and external query failures:
  ```bash
  # Distance, bearing, and cardinal direction between two GPS coordinates:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py distance <lat1> <lon1> <lat2> <lon2>

  # Solar elevation, solar azimuth, and shadow orientation for triangulation:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py sun-position <lat> <lon> "YYYY-MM-DD HH:MM:SS"

  # Query named summits, ridges, lakes, streams, and valleys within radius (default 12 km) via USGS GNIS Layers 5/6/7 + OSM:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py nearby-features <lat> <lon> --radius-km 15.0

  # Compute 3DEP DEM topographic horizon (including UNNAMED peaks/ridges), named summit visibility up to 22+ km, rectilinear lens FOV pixel-X projections (0.00=left edge, 0.50=center, 1.00=right edge), AND render an annotated 2D synthetic DEM panorama:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py horizon-skyline <lat> <lon> --radius-km 22.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> --out-pano "scratch/<photo_stem>_pano.png"

  # Or render a standalone 2D front-to-back ray-marched synthetic DEM panorama (with azimuth/altitude degree grid, distance-band & meadow coloring, FOV bounds, and peak labels):
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py render-pano <lat> <lon> --out "scratch/<photo_stem>_pano.png" --center-az <est_azimuth> --hfov-deg <hfov_deg>

  # Download and annotate a USGS Topo map with camera marker, optional FOV wedge, bearing rays, and nearby summits:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png" --radius-km 5.0 --center-az <est_azimuth> --hfov-deg <hfov_deg>

  # Topographic elevation profile & line-of-sight occlusion check between camera and candidate target (40-step 3DEP DEM profile):
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py elevation-profile <cam_lat> <cam_lon> <target_lat> <target_lon>
  ```

---

## Analysis Steps

1. **Location, Date & Time**:
   - Extract the timestamp and GPS coordinates using `extract_metadata.py`.
   - Identify the exact geographic location: wilderness area, national forest, state/national park, mountain range, and specific trail.
   - **Trail Junction Check**: When photos are taken near trail intersections, do not simply snap to the trailhead trail name. Check whether the route has branched onto a secondary trail.
   - If EXIF or GPS data is missing, note `[Not available in EXIF]` and estimate from visual and chronological context.

2. **Elevation**:
   - Camera GPS altitude is often ellipsoidal or barometric and can deviate from actual ground level by 50 – 150 ft.
   - Use `extract_metadata.py` (with optional `--ref-elev-ft "Label:ElevFt"` flags) to retrieve official ground elevation from USGS 3DEP EPQS and pre-computed benchmark deltas.
   - Always report elevation in dual units: `XXX ft / YYY m`. Include relative elevation context whenever relevant (e.g. `~XXX ft / YYY m above trailhead; ~YYY ft / ZZZ m below summit`).

3. **Features & Line-of-Sight Verification**:
   - Identify specific visible natural features: peaks, ridges, cirques, lakes, tarns, saddles, and meadows.
   - **Omnidirectional Feature Discovery (No One-Sided Ray Tracing)**:
     - Always run `geo_tools.py nearby-features <lat> <lon>` before testing individual peaks. It returns all named summits, ridges, lakes, streams, and valleys in 360° sorted by distance with their bearings and elevations.
     - Do not restrict queries, sightlines, or ray-tracing to a single preconceived quadrant.
   - **Anti-Narrative-Anchoring Rule**:
     - Use the blog draft (`post.md`) for hike context and draft placement, but **never** assume a photo points in the same direction as a nearby sentence in `post.md` (e.g., if `post.md` says *"From the saddle, the view opened up to the east"*, the hiker may still have photographed a northwest or look-back vista from that saddle).
     - Physical geometry (solar shadows, travel vector, and topographic elevation profiles) **always** overrides directional prose in `post.md`.
   - **Mandatory Travel-Vector Framing**:
     - Inspect the `prev_photo` and `next_photo` walking vectors automatically returned by `extract_metadata.py` (or compute between arbitrary coordinates via `geo_tools.py distance <prev_lat> <prev_lon> <lat> <lon>`).
     - Most trail and ridgeline photos are captured along natural axes. Prioritize candidate camera orientations in this order:
       1. **Forward** along the travel vector ($\text{bearing} \pm 45^\circ$).
       2. **Lateral** vistas looking off the trail or ridgeline ($\text{bearing} \pm 90^\circ$).
       3. **Look-backs** along the reverse path ($\text{bearing} + 180^\circ \pm 45^\circ$).
   - **Rigorous Solar Shadow Verification**:
     - Calculate solar azimuth and shadow direction using `geo_tools.py sun-position <lat> <lon> "<timestamp>"`.
     - Cross-check candidate camera bearings against physical shadow orientation using the relative sun angle:
       $$\text{Relative Sun Angle} = (\text{Solar Azimuth} - \text{Camera Bearing}) \pmod{360^\circ}$$
       - $150^\circ – 210^\circ$ (sun behind camera): shadows cast forward into the scene away from the photographer.
       - $330^\circ – 30^\circ$ (sun in front of camera): scene is backlit / prone to glare, shadows fall toward the camera. **Backlit Morning/Evening Check**: When solar altitude is low ($<25^\circ$) and the camera points within $\pm 30^\circ$ of the sun azimuth, tree trunks and boulders facing the camera will have **dark shadowed front faces with bright rim-lit edges**, and ground shadows will point *toward* the bottom of the frame. Do not confuse into-the-sun rim lighting with cross-lighting!
       - $60^\circ – 120^\circ$ (sun on right flank): shadows cast sharply to the left.
       - $240^\circ – 300^\circ$ (sun on left flank): shadows cast sharply to the right.
     - Shadows are a physical constraint: never rationalize shadow directions to fit an assumed landmark.
   - **Horizon Skyline, Synthetic DEM Panorama, Unnamed Peaks, Rectilinear FOV & Line-of-Sight Verification**:
     - Whenever mountain ridges, peaks, meadows/basins, or canyon walls are visible, run `geo_tools.py horizon-skyline <lat> <lon> --radius-km 22.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> --out-pano "scratch/<photo_stem>_pano.png"` (using `estimated_single_frame_hfov_deg` from `extract_metadata.py`, e.g., `54.4` for a 35mm lens).
     - **Visual Alignment with Synthetic DEM Panorama (`render-pano` / `--out-pano`)**: View the generated `scratch/<photo_stem>_pano.png` with `read_file` alongside the photo. It renders a front-to-back 2D DEM ray-marched perspective view (`Altitude angle` vs. `Azimuth`) shaded by distance band (`<2 km` dark green foreground, `2–6 km` slate-green mid-ground, `6–12 km` slate-blue, `>12 km` pale blue) and highlights flat basins/meadows below the camera in gold, with annotated callouts for visible named features and unnamed DEM horizon highpoints. Use it to lock in the exact camera azimuth (`±1°`) by matching foreground slopes, basin floors, canyon notches, and skyline silhouettes.
     - **Enforce Rectilinear Lens FOV Geometry**: Check `in_fov` and `projected_x_fraction` (`0.00` = left frame edge, `0.50` = horizontal center, `1.00` = right frame edge) for every candidate peak. A summit near the horizontal center of the image (`projected_x_fraction` $\approx 0.35 – 0.65$) **cannot** be $35^\circ – 40^\circ$ off the camera's center azimuth, and any peak with `in_fov: false` is outside the frame even if mentioned in `post.md`.
     - **Check for Unnamed Horizon Peaks**: USGS GNIS and OpenStreetMap only list *named* summits. Frequently, the prominent peak or ridge crest in the frame is an **unnamed topographic highpoint** (e.g., `Peak 11,062 ft / 3,372 m`). Always inspect `dem_horizon_highpoints` in `horizon-skyline` (and the blue `Peak ~XXXXft` callouts on `scratch/<photo_stem>_pano.png`): if an `Unnamed Horizon Highpoint` sits at the exact bearing and vertical angle of the mountain in the photo while the nearest named summit is $25^\circ+$ off-axis or `OCCLUDED`, identify it by its elevation and ridge location rather than forcing a misattribution to the named summit.
     - **Line-of-Sight & Occlusion Check**: Never assume a prominent landmark is visible without verifying line-of-sight (`VISIBLE` vs. `OCCLUDED` in `horizon-skyline` or `geo_tools.py elevation-profile <cam_lat> <cam_lon> <target_lat> <target_lon>`). If `line_of_sight` returns `UNKNOWN (INCOMPLETE_ELEVATION_DATA)`, do not assume `VISIBLE`.
     - For lakes, verify both line-of-sight visibility and shoreline shape/orientation against the visible water body (or run `geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png"`).
   - For distant features, crop the region using `crop_photo.py` before finalizing identification.

4. **Primary Subject**:
   - Determine the focal point of the composition (e.g., a specific summit reflection, an alpine lake basin, an isolated wildflower, a geological formation).

5. **Surrounding & Setting**:
   - Characterize lighting conditions (e.g., alpenglow, midday alpine glare, harsh backlight, golden hour) and terrain environment (e.g., glaciated granodiorite shelf, talus slope, subalpine fir meadow).

6. **Fauna & Flora Identification**:
   - For close-ups of plants, fungi, lichens, insects, or wildlife, use `crop_photo.py` to inspect diagnostic details (leaf margins, needle arrangements, berry morphology, pore surfaces, wing patterns).
   - Cross-reference visual details with elevation, geographic range, and seasonal timing to identify the species or subspecies when diagnostic characters are clearly visible.
   - **Visual-Phenotype & Life-Stage Consistency Check**:
     - Before committing to a species name, verify that **every** visual trait implied by its common or scientific name (e.g., the red band of a "Western Red-Belt Conk", flower color, leaf arrangement, or intact wing shape) is visibly present in the photo.
     - **Handling Juvenile or Lookalike Taxa**: If a specimen is in a juvenile/primordium stage lacking adult diagnostic markers (e.g., a pale cushion-stage *Fomitopsis* conk with no red belt) or cannot be macroscopically separated from a sympatric sister species (e.g., *Fomitopsis mounceae* vs. *F. ochracea*, or vegetative *Vaccinium* species), **explicitly state the life stage and sister-species ambiguity** (e.g., `Young Polypore Conk (*Fomitopsis* complex, likely juvenile *F. mounceae* or *F. ochracea*)`) and explain why the diagnostic color/trait is absent rather than asserting an unqualified species name.
   - Be wary of optical artifacts and shadows: camera angles or cast shadows may mimic missing wings or altered colors on insects and wildlife.

---

## Augmentation & Editorial Constraints

- **Dual Units (Mandatory)**:
  - All elevations MUST be formatted as `XXX ft / YYY m`.
  - All temperatures MUST be formatted as `XX°F / YY°C`.
- **Dashes (Mandatory)**:
  - Never use em-dashes (`—`). Always use spaced en-dashes (` – `).
- **Azimuth vs. Distance**:
  - Never include camera azimuth or degree headings in public descriptions or captions.
  - Do include estimated distance from the camera (e.g., `~0.8 mi / 1.3 km to the southwest`) to give the reader a tangible sense of scale.
- **Concise Bullets**:
  - Augment identified features with concise facts regarding geology, glacial history, ecology, or trail context. Cap augmentation at 2 – 3 bullet points per feature.
- **Orchestration Boundary**:
  - Subagents analyzing photos must **never** read or append directly to a shared, monolithic `catalog.md` (which causes context bloat and line-range truncation errors).
  - Instead, return the completed markdown block directly in the final response for the orchestrator to incorporate.

---

## Scripting & Computation Guidelines

- **Use Packaged Tooling First**: Rely on bundled skill scripts (`scripts/extract_metadata.py`, `scripts/crop_photo.py`, `scripts/geo_tools.py`) for metadata extraction, coordinate formatting, elevation resolution, detail zooming, spatial bearings/distances, solar orientation, nearby feature lookups, 3DEP DEM horizon skylines (`horizon-skyline`), annotated USGS Topo maps (`render-topo`), and line-of-sight elevation profiles (`elevation-profile`). Do not read `geo_tools.py` source code to import its internal functions into custom scripts.
- **Synchronous Wait Timeout (`WaitMsBeforeAsync: 10000`) & No `manage_task` Polling**: When invoking `extract_metadata.py` or `geo_tools.py` via `run_command`, always set `WaitMsBeforeAsync: 10000` (10 seconds). Because 3DEP DEM rasters and directory EXIF batches are cached locally, queries complete in 1–4 seconds. If a command ever moves to the background, **never** call `manage_task(Action="status")` in a loop or read `task-XX.log`; simply end your turn and wait for the system wakeup notification.
- **Photo-Prefixed `scratch/` Filenames (Concurrent Subagent Safety)**: Multiple subagents run concurrently in the same workspace. Every image crop, annotated topo map, or diagnostic script saved to `scratch/` **must** be prefixed with the photo stem (e.g., `scratch/281A5985_topo.png`, `scratch/281A5985_crop.jpg`)—never generic filenames like `scratch/topo_map.png`, `scratch/dem.tif`, or `scratch/download_topo.py`.
- **Mental Math for Standard Conversions**: Perform routine scalar conversions (feet to meters, Fahrenheit to Celsius) directly in internal reasoning rather than spawning one-line Python shells.
- **File-First Execution (No Inline `python -c` with Quotes)**: Never execute Python one-liners containing nested quotes, dictionaries, regex, or multi-line strings via `python -c`. Always write the code to a photo-prefixed `.py` file in `scratch/` using file-creation tools and execute `python <file_path>` instead.
- **Host Shell Escaping (PowerShell)**: The host shell is Windows PowerShell, where the escape character is the backtick (`` ` ``), NOT the backslash (`\`). Avoid passing complex CLI arguments with nested single and double quotes (such as ExifTool format strings) directly on the command line; use packaged scripts or file-based Python scripts instead.
- **USGS Topo Image Mode**: Prefer `geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png"` to download and annotate USGS Topo maps automatically. If ever opening a USGS Topo PNG manually with `PIL.ImageDraw`, always call `img = img.convert("RGB")` before drawing, as exported PNGs use an 8-bit color palette (`mode='P'`) that raises `ValueError: cannot allocate more than 256 colors`.
- **Avoid Trivial Print Probes**: Do not spawn Python one-liners solely to echo static variables, test string formatting, or print basic arithmetic. Write cohesive scripts when data retrieval, API queries, or spatial calculations are genuinely needed.
- **Diagnostic Scripts Permitted for Debugging**: Targeted diagnostic prints and schema inspections are fully permitted when troubleshooting API errors, parsing complex track files (TCX/GPX), or diagnosing unexpected data formats.

---

## Output Format

Always return the analysis in this concise markdown template:

```markdown
### Photo: `<filename>`
- **Date & Time Taken**: [e.g. September 10, 2026 at 08:49:07 PDT]
- **GPS Coordinates**: [coordinates in DMS format, e.g. 45°13'56.95" N 117°21'37.91" W]
- **Elevation**: [XXX ft / YYY m, plus relative elevation context if applicable, e.g. ~1,080 ft / 329 m above trailhead]
- **Location**: [Precise geographic location, specific trail name and number, wilderness, county, state]

### Identified Natural Features

- **[Feature Name]**:
  - [Visual description, distance from camera, and geological or geographic context]
  - [Relevant factual details capped at 2 – 3 concise bullets]

### Identified Fauna & Flora

- **[Common Name (*Scientific Name*)]**:
  - [Visual morphology observed from crop/photo, habitat context, and distinguishing characteristics]

### Suggested Caption

[Caption no longer than 3 sentences describing the focal subject and setting. Conclude every caption with its GPS coordinates in parentheses, e.g. (45°13'56.95" N 117°21'37.91" W)]

### Suggested Draft Placement

[Suggest which section or narrative beat of the blog post draft this photo best illustrates.]
```
