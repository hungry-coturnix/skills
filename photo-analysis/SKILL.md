---
name: photo-analysis
description: >-
  Extracts structured visual details, setting, and subjects from 
  hiking and travel photos for integrating into blog posts. Use
  whenever analyzing photos to match with blog draft sections.
---

# Hiking Photo Analysis

Analyze a hiking photo and produce a concise, structured visual summary without bloating conversation context, augmented with verified geographic, botanical, and geological details visible in the frame.

## Helper Scripts

All CLI flags and output JSON schemas for `scripts/` are documented below so you **never** need to run `--help` or inspect script source code:

- **`scripts/extract_metadata.py`**: Runs ExifTool (batch-cached), queries USGS 3DEP ground elevation and administrative boundaries, validates `--ref-elev-ft` benchmarks against local USGS/3DEP features, detects stitched panoramas, and computes preceding/succeeding hike travel vectors:
  ```bash
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\extract_metadata.py "<photo_path>" --ref-elev-ft "Trailhead:5267" --ref-elev-ft "High Point:7990"
  ```
  - **CLI Flags**: `<photo_path>`, `--ref-elev-ft "Label:ElevFt"` (repeatable).
  - **Output JSON Keys**:
    - Core & coordinates: `photo_path`, `date_time`, `gps_dms`, `lat`, `lon`, `missing_gps_guidance` (emitted when `lat`/`lon` is `null` to warn when `prev_photo` is `null` or `elapsed_time_min > 15` to read `post.md` before `geo_tools.py`)
    - Elevation & benchmarks: `ground_elevation_ft`, `ground_elevation_m`, `exif_elevation_ft`, `exif_elevation_m`, `elevation_formatted`, `elevation_deltas` (1-line dict of `"Label (X,XXX ft)": "~X,XXX ft / X,XXX m above/below"`), `benchmark_warnings` (emitted when any `--ref-elev-ft` benchmark disagrees with a matching local USGS/3DEP feature)
    - Camera, lens & exposure: `camera_model`, `lens_model`, `focal_length_mm`, `focal_length_35mm`, `estimated_single_frame_hfov_deg`, `exposure_time`, `f_number`, `iso`, `camera_spec_formatted`, `image_width`, `image_height`
    - Panorama detection: `is_stitched_panorama_candidate`, `panorama_guidance`
    - Neighbor & trajectory vectors: `prev_photo`, `next_photo` (skipping stationary frames `<4.0 m`; each has `filename`, `date_time`, `lat`, `lon`, `elevation_ft`, `elevation_m`, `elevation_change_ft`, `elevation_change_m`, `trail_gradient`, `elapsed_time_min`, `travel_vector_from_prev` / `travel_vector_to_next`), `prev_hike_stops`, `next_hike_stops` (up to 3 preceding/succeeding stops `>= 25 m` or `>= 45 s`, each with `file`, `lat`, `lon`, `elev_ft`, `dz_ft`, `dt_min`, `dist_m`, `bearing_deg`, `cardinal`)
    - Administrative boundaries: `admin_boundaries` (County, State, National Park, Wilderness Area, National Forest)
  - Never run `exiftool.exe` separately after `extract_metadata.py`. *Fallback*: `exiftool.exe -d "%Y-%m-%d %H:%M:%S" -DateTimeOriginal -GPSPosition -c "%d°%d'%.2f\"" -GPSLatitude# -GPSLongitude# -GPSAltitude# -FocalLength# -FocalLengthIn35mmFormat# "<photo_path>"`

- **`scripts/crop_photo.py`**: Crops high-resolution images (auto-converting paletted/RGBA to RGB) to inspect fine details (flora, distant peaks, shadows, junctions, lake shapes) without downsampling:
  ```bash
  # Combine multiple native-resolution --box crops (and optional 2000px --overview) with --grid in one call:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --overview 2000 --grid --box 0.2 0.3 0.6 0.7:peak --box 0.4 0.6 0.8 0.9:shadow --out "scratch/<photo_stem>_crop.jpg"
  # Extract all N horizontal panorama slices (or --slice K N) with coordinate grid:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --all-slices 3 --grid --out "scratch/<photo_stem>_slice.jpg"
  ```
  - **CLI Flags**:
    - `--box X1 Y1 X2 Y2[:label]` (repeatable; fractional `0.0–1.0` or pixel coords; stays at native resolution with `--overview 2000`)
    - `--overview [MAX_DIM]` and `--max-dim MAX_DIM` (default `2000 px`)
    - `--grid` (1px dashed gridlines every 5%/10% with fractional `x=0.05..0.95`, `y=0.05..0.95` and pixel `x_px`, `y_px` labels in a `36 px` outer collar)
    - `--all-slices N` (`--slices N`) or `--slice K N`
    - `--region {center,top-left,top-right,bottom-left,bottom-right,left,right}`
    - `--out <path>` (writes `{stem}_{label}{ext}` alongside `--out` and returns `"out_path"` and `"labeled_out_path"` in `stdout` JSON—always read the exact `out_path` from `stdout`)

- **`scripts/geo_tools.py`**: Unified geographic, 3DEP DEM horizon skyline, line-of-sight, azimuth calibration, topo map rendering, and solar/lunar utility (`python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py <subcommand> ...`):
  ```bash
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py distance <lat1> <lon1> <lat2> <lon2>
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py sun-position <lat> <lon> "YYYY-MM-DD HH:MM:SS" [--center-az <est_azimuth>] [--out-json "scratch/<photo_stem>_sun.json"]
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py nearby-features <lat> <lon> --radius-km 15.0 [--types <Type1,Type2,...>] [--az-min <deg>] [--az-max <deg>] [--max-dist-mi <mi>] [--max-dist-km <km>] [--search "<term1,term2,...>"] [--max-results <int>] [--verbose] [--out-json "scratch/<photo_stem>_nearby.json"]
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py horizon-skyline <lat> <lon> --radius-km 22.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> [--az-min <deg>] [--az-max <deg>] [--projection {rectilinear,cylindrical}] [--check-coords "Name:lat,lon" | "<Name1,Name2>"] [--types <Type1,Type2>] [--max-dist-mi <mi>] [--max-dist-km <km>] [--search "<term1,term2,...>"] [--verbose] [--image-width <px>] --out-pano "scratch/<photo_stem>_pano.png" [--out-topo "scratch/<photo_stem>_topo.png" [--topo-radius-km 2.0]] --out-json "scratch/<photo_stem>_skyline.json"
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py render-pano <lat> <lon> --out "scratch/<photo_stem>_pano.png" [--center-az <est_azimuth>] [--hfov-deg <hfov_deg>] [--az-min <deg>] [--az-max <deg>] [--alt-min <deg>] [--alt-max <deg>] [--radius-km 22.0] --out-json "scratch/<photo_stem>_pano.json"
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png" --radius-km 1.5 --center-az <est_azimuth> --hfov-deg <hfov_deg> [--rays <b1,b2,...>] [--out-json "scratch/<photo_stem>_topo.json"]
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py elevation-profile <cam_lat> <cam_lon> [<target_lat> <target_lon>] [--bearing-deg <deg> (--dist-km <km> | --dist-mi <mi>)] [--samples 40 | --steps 40] [--full-profile] [--out-json "scratch/<photo_stem>_prof.json"]
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py calibrate-azimuth --hfov-deg <hfov_deg> [--anchor <bearing_deg>:<x_fraction> ...] [--center-az <deg>] [--x-frac <f1,f2,...> ...] [--project-bearings "Name1:b1" "Name2:b2" ...] [--projection {rectilinear,cylindrical}] [--solve-hfov]
  ```
  - **Subcommand Flags & Output JSON Keys**:
    - `sun-position`: Keys: `solar_azimuth_deg`, `solar_altitude_deg`, `shadow_azimuth_deg`, `flat_ground_2d_shadow_by_camera_octant`, `observed_2d_shadow_clock_to_camera_bearing`, `terrain_slope_caveat`, plus lunar keys `lunar_altitude_deg`, `lunar_azimuth_deg`, `lunar_cardinal`, `lunar_illumination_fraction` (`0.0–1.0`), `lunar_phase_name`, `is_moon_above_horizon` (anchor `calibrate-azimuth` with `lunar_azimuth_deg` when the Moon is visible above the local 3DEP DEM horizon; never import `astropy`, `skyfield`, `ephem`, `pyephem`, or `pysolar`).
    - `nearby-features`: `--radius-km` (`15.0` unfiltered in Stage 1; `40.0` across broad valleys), `--types` (case-insensitive: `Summit`, `Peak`, `Ridge`, `Gap`, `Pass`, `Valley`, `Basin`, `Flat`, `Lake`, `Stream`, `Spring`, `Glacier`, `Cliff`, `Pillar`, `Falls`, `Range`, `PopulatedPlace`), `--az-min`, `--az-max` (360° wrap-aware), `--max-dist-mi`, `--max-dist-km`, `--search "<term1,term2,...>"` (pass comma-separated substrings in one call, e.g., `--search "Twin Lakes,Granite Pass"`, rather than multiple `--search` calls; auto-includes distant `Stream`/`Spring`), `--max-results`, `--verbose` (only needed for distant `>5 km` minor `Flat`/`Bench`), `--out-json <path>` (writes full catalog to disk while compacting `stdout` to `<=5,450 chars` with fixed item keys: `name`, `type`, `distance_mi`, `distance_km`, `bearing_deg`, `cardinal`, `elevation_ft`, `elevation_m`, `lat`, `lon`).
    - `horizon-skyline`: `--radius-km` (`22.0` default; `40.0` across broad valleys), `--center-az`, `--hfov-deg`, `--az-min`, `--az-max` (cylindrical sector bounds, NOT feature filters), `--projection {rectilinear,cylindrical}` (auto `cylindrical` when `--hfov-deg > 100` or sector `< 360°`), `--check-coords "Name:lat,lon" | "<Name1,Name2>"` (repeatable; accepts `Name:lat,lon` or bare comma-separated feature names), `--types`, `--max-dist-mi`, `--max-dist-km`, `--search "<term1,term2,...>"`, `--verbose`, `--image-width`, `--out-pano <path>`, `--out-topo <path>` (`--topo-radius-km`, default `2.0`), `--out-json <path>` (`--json-out`). Top-level keys: `camera`, `fov`, `dem_warning`, `checked_coords_visibility`, `visible_features_in_fov` (`FOV ± 20°`), `dem_horizon_highpoints` (`>=500m`, `FOV ± 15°`), `foreground_ridges` (`50m–500m` local maxima, `FOV ± 15°`), `top_dominant_360_visible_summits`, `synthetic_pano`, `topo_map`, `occluded_features_in_fov` (`occluded_in_fov_total_count`; full list in `--out-json`), `named_features_visibility` (in `--out-json`). Item keys: `name`, `type`, `bearing_deg`, `cardinal`, `distance_mi`, `distance_km`, `elevation_ft`, `elevation_m`, `target_vertical_angle_deg`, `clearance_deg`, `in_fov`, `projected_x_fraction`, `line_of_sight` (`"VISIBLE"`, `"OCCLUDED"`, `"CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"`), `visibility_class` (`"GRAZING_TIP_ONLY"` when `0.0 <= clearance_deg < 0.25`), `highest_intervening_ridge`, `occluded_by`, `matched_named_feature`, `nearest_named_feature` (`<=2.5 km` for unnamed `dem_horizon_highpoints`), `lat`, `lon`.
    - `elevation-profile`: Keys: `distance_mi` / `total_distance_mi`, `distance_km` / `total_distance_km`, `bearing_deg`, `cardinal`, `camera_elevation_ft`, `target_elevation_ft`, `target_vertical_angle_deg`, `max_intervening_angle_deg`, `clearance_deg`, `line_of_sight`, `visibility_class`, `visibility_note`, `highest_intervening_ridge`, `occluded_by` / `occluding_ridge`, `key_ray_inflections` (`<=8`-item inflection summary in `stdout`), `profile` (in `--out-json`).
    - `render-pano` & `render-topo`: `render-pano` renders standalone 2D synthetic DEM panoramas (`--out-json` compacts `stdout` to `<=5,400 chars`). `render-topo` renders annotated USGS Topo maps (`--radius-km 1.5` or `1.5–2.0` near small lakes/tarns/switchbacks; always draws 12 subtle 30° compass rays `0°..330°` around `CAMERA` and caches the USGS Topo basemap in `%TEMP%`).
    - `calibrate-azimuth`: `--anchor <bearing_deg>:<x_fraction>` (solves `center_azimuth_deg`, `left_edge_deg`, `right_edge_deg`), `--x-frac` (`x_fraction_projections`), `--project-bearings` (bearings or `"Name:bearing"`; `bearing_projections` with `in_fov` and `projected_x_fraction`), `--projection {rectilinear,cylindrical}`, `--solve-hfov`. With `--projection cylindrical` (or `--solve-hfov`) and $\ge 2$ wide-spread anchors ($\Delta x \ge 0.35$, implied HFOV $> 1.15 \times \text{hfov\_deg}$), auto-solves stitched `hfov_deg` and `center_azimuth_deg` via linear regression. Anchor near the horizon; never re-run for tweaks `< 1.5°`.

---

## Analysis Steps

1. **Location, Date & Time**:
   - Extract timestamp, GPS coordinates, and `admin_boundaries` via `extract_metadata.py`. Identify the wilderness area, national forest, state/national park, mountain range, and trail (checking near trail junctions whether the route branched).
   - If EXIF GPS is missing (`lat: null, lon: null`), note `[Not available in EXIF]`, follow the Stage 1 Missing-EXIF-GPS exception below, and keep your estimated `(lat, lon)` fixed across `geo_tools.py` calls rather than jittering by `0.01°` (which busts the `0.01°` grid cache).

2. **Elevation & Benchmark Reconciliation**:
   - Retrieve official USGS 3DEP ground elevation and benchmark deltas via `extract_metadata.py` (`--ref-elev-ft "Label:ElevFt"`). Report elevation in plain-text dual units: "XXX ft / YYY m", with relative context ("~XXX ft / YYY m above trailhead").
   - **Benchmark Precedence & `benchmark_warnings`**:
     1. When a landmark elevation is provided via `--ref-elev-ft` and `extract_metadata.py` does **not** flag it in `benchmark_warnings`, use that benchmark consistently across `Elevation`, `Identified Natural Features`, and `Suggested Caption`. If `extract_metadata.py` emits a `benchmark_warnings` entry for a feature, use the local USGS/3DEP elevation instead and flag the discrepancy in `Suggested Draft Placement`.
     2. **Stage 2 `post.md` DEM-Smoothing Check**: Because 3DEP DEM grid cells smooth sharp summits by `10–150 ft` (and up to `250 ft` on narrow rock pinnacles), if `post.md` cites a summit/pass elevation within that margin above the DEM sample, adopt `post.md`'s benchmark. If `post.md` cites an elevation lower than the DEM sample or off by `> 150–250 ft`, keep the USGS/DEM elevation and flag the discrepancy in `Suggested Draft Placement`.
     3. **Single Elevation Per Bold Feature Header**: Each bold feature header under `### Identified Natural Features` may contain **at most one** dual-unit elevation (`- **Feature Name (X,XXX ft / X,XXX m)**:`). Put any secondary sub-peak, shoulder, or individual lake-surface elevations in sub-bullet prose. Never copy example elevations from `SKILL.md`.

3. **Features & Line-of-Sight Verification**:
   - **Two-Stage Geometry-First Ordering (Anti-Narrative-Anchoring Rule)**:
     - **Stage 1 (Unbiased Physical & Geometric Discovery — Before Reading `post.md`)**: First run `extract_metadata.py`, view the photo (`read_file`), run `sun-position` and unfiltered `nearby-features <lat> <lon> --radius-km 15.0 --out-json "scratch/<photo_stem>_nearby.json"` (or `--radius-km 40.0` across broad valleys), and run your initial `horizon-skyline --out-pano` and `render-topo` pass as **separate parallel `run_command` calls** based purely on visual terrain, solar shadows, and travel vectors. Do **NOT** read `post.md` during Stage 1.
       - **Exception When EXIF GPS Is Missing (`lat: null, lon: null`)**: If `extract_metadata.py` reports `lat: null` (or emits `"missing_gps_guidance"`) and `prev_photo` is `null` or `elapsed_time_min > 15`, read `post.md` immediately before running `geo_tools.py` to determine whether the photo was taken at a pre-hike camp, shuttle trailhead, or highway viewpoint rather than guessing `next_photo` coordinates.
     - **Stage 2 (Narrative Cross-Check, Elevation Reconciliation & Draft Placement — Mandatory `post.md` Read)**: Once initial geometric orientation is established, **read `post.md`** to confirm trail/junction context, test landmarks mentioned near that segment, reconcile elevations per Step 2, and determine `Suggested Draft Placement` (reading line and narrative paragraph numbers directly from `read_file` of `post.md`—never writing custom paragraph-counting scripts). Physical geometry **always** overrides directional prose in `post.md`.
   - **Omnidirectional Discovery, Cross-Valley Radius & `VISIBLE` vs. `in_fov`**:
     - Keep Stage 1 `nearby-features` **unfiltered** so foreground creeks (`Stream`), waterfalls (`Falls`), glaciers, lakes, and summits are all captured.
     - **Cross-Valley Viewshed Radius (`--radius-km 40.0`)**: Keep `--radius-km 22.0` as the Stage 1 default inside alpine basins and canyons, but pass **`--radius-km 40.0`** to **both** Stage 1 `nearby-features` and `horizon-skyline` whenever looking out across a broad intermountain valley (`25–40 km` viewshed) so both share a single `%TEMP%` feature and DEM cache key and capture distant mountain crests and valley towns (`PopulatedPlace`).
     - `line_of_sight: "VISIBLE"` is azimuth-independent from `(lat, lon)`, whereas `in_fov: false` depends on your `--center-az` guess. Always inspect `top_dominant_360_visible_summits` in `horizon-skyline` (and any `VISIBLE` peak in `post.md`).
   - **Travel-Vector Framing, Switchbacks & Topographic Drainage Direction**:
     - Test candidate orientations from `prev_photo`, `next_photo`, `prev_hike_stops`, `next_hike_stops`, and `trail_gradient` in order: (1) **Forward** ($\text{bearing} \pm 45^\circ$), (2) **Lateral** ($\text{bearing} \pm 90^\circ$), (3) **Look-backs** ($\text{bearing} + 180^\circ \pm 45^\circ$). On switchbacks/traverses, local trail direction can differ $60^\circ–180^\circ$ from the macro GPS chord; test both when vectors diverge.
     - **Chronological Bearing & Drainage Direction Rule**: If a landmark's bearing in `nearby-features` lies `~180°` behind your forward walking vector, you have **already passed** it—never describe the photo as "approaching" or "ascending toward" it. Align *"up"* vs. *"down"* a canyon, valley, or trough with its **hydrological drainage gradient** rather than camera pitch: looking upstream toward higher headwaters or a pass is looking **up** the canyon/trough (even when perched high on a slope looking down onto a nearer lake floor), whereas looking downstream toward lower reaches is looking **down** the canyon/trough.
   - **Rigorous Solar Shadow Verification**:
     - Run `sun-position <lat> <lon> "<timestamp>" [--center-az <est_azimuth>]` (`camera_azimuth ≈ (shadow_azimuth - image_shadow_clock_offset) % 360`, $\Delta\text{az} = (\text{solar\_azimuth} - \text{camera\_azimuth}) \bmod 360^\circ$):
       - **12 o'clock (`0°`)** $\rightarrow$ `camera_azimuth ≈ shadow_azimuth` ($\Delta\text{az} \approx 180^\circ$, sun behind); **3 o'clock (`+90°`)** $\rightarrow$ `camera_azimuth ≈ (shadow_azimuth - 90) % 360` ($\Delta\text{az} \approx 270^\circ$, sun on left); **9 o'clock (`-90°`)** $\rightarrow$ `camera_azimuth ≈ (shadow_azimuth + 90) % 360` ($\Delta\text{az} \approx 90^\circ$, sun on right).
       - **6 o'clock / toward bottom (`180°`)** $\rightarrow$ `camera_azimuth ≈ solar_azimuth` ($\Delta\text{az} \approx 0^\circ$, backlit; at solar altitude $<25^\circ$ within $\pm 30^\circ$ of sun, camera-facing trunks/boulders have **dark shadowed front faces with bright rim-lit edges** and shadows point toward the bottom—never confuse rim lighting with cross-lighting).
       - **Diagonal Up-Left (`10–11 o'clock`, sun behind-right $\Delta\text{az} \in [110^\circ, 150^\circ]$) vs. Down-Left (`7–8 o'clock`, dark front faces with rim-lit right/rear edges, sun in front-right $\Delta\text{az} \in [15^\circ, 75^\circ]$)**: Never confuse shadows falling toward the lower-left foreground with `-90°` cross-lighting or behind-right lighting.
       - **Slope Caveat**: Steep 3D slopes ($20^\circ–35^\circ+$) and wide-angle corners rotate 2D shadow clock angles by $\pm 15^\circ–35^\circ$; treat `sun-position` as a $\pm 35^\circ$ octant cue on steep terrain and lock exact azimuth via `horizon-skyline`.
   - **Horizon Skyline, Synthetic DEM Panorama & Geometric Guardrails**:
     - When terrain/skyline is visible, run `horizon-skyline <lat> <lon> --radius-km 22.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> --out-pano "scratch/<photo_stem>_pano.png" --out-json "scratch/<photo_stem>_skyline.json"`. If pointing steeply downward at immediate ground/flora (`< 10 ft`) with **no horizon or midground/background terrain visible**, skip `horizon-skyline` and `calibrate-azimuth` (run only `render-topo --radius-km 1.5` if needed).
     - **Stitched Panorama (`-Pano.jpg`) & Steep Canyon Workflows**: When `is_stitched_panorama_candidate: true`, run one wide `horizon-skyline --az-min <min> --az-max <max> --projection cylindrical --out-pano "scratch/<photo_stem>_pano.png" --out-json "scratch/<photo_stem>_skyline.json"` and `crop_photo.py --all-slices 3 --grid` (and use `calibrate-azimuth --projection cylindrical` with $\ge 2$ wide-spread anchors $\Delta x \ge 0.35$ to auto-solve stitched `hfov_deg` and `center_azimuth_deg`). Inside steep V-shaped canyons where near walls (`0.3–1.5 km`) dominate or azimuth is uncertain, run `render-pano --az-min 0 --az-max 360 --out "scratch/<photo_stem>_pano_360.png" --out-json "scratch/<photo_stem>_pano_360.json"`.
     - **Visual Alignment with `<photo_stem>_pano.png` & Calibration Shift Rules**:
       - View `scratch/<photo_stem>_pano.png` with `read_file` alongside the photo to match distance-shaded bands (`<0.5 km` to `>12 km`), gold basin floors, `[TIP ONLY +0.XX deg]` flags, and blue `Peak ~XXXXft` unnamed highpoints.
       - Do **not** re-run `calibrate-azimuth` for `< 1.5°` tweaks. Do **not** re-run `horizon-skyline` or re-render `<photo_stem>_pano.png` if `center_azimuth_deg` shifts by **`<= 4°`**. For **`4°–10°`** shifts, run `calibrate-azimuth --center-az <cal_az> --hfov-deg <hfov> --project-bearings "Name1:b1" "Name2:b2"` to update `x_fraction` positions without re-rendering `_pano.png`. Only re-run `horizon-skyline` when off by **`> 10°`**, writing to a distinct filename (`--out-pano "scratch/<photo_stem>_pano_cal.png"`).
       - Never re-run `render-topo` or re-read `<photo_stem>_topo.png` merely because `--center-az` or `--hfov-deg` changed (the 30° compass rays and plotted landforms already cover 360°); only re-run `render-topo` if you change `(lat, lon)` or zoom `--radius-km` (e.g., from `5.0` to `1.5` for a shoreline/switchback).
     - **2D (`projected_x_fraction` + `clearance_deg` & `GRAZING_TIP_ONLY`) Verification**: Check `in_fov` and `projected_x_fraction` (`0.00` = left, `0.50` = center, `1.00` = right; `in_fov: false` is outside the frame). Verify vertical angular relief (`clearance_deg`, where $\Delta y / H \approx \text{clearance\_deg} / \text{VFOV}$; a peak $>7\text{ mi}$ away with $<1.0^\circ$ clearance spans $<2.5\%$ of image height). When `visibility_class` is `GRAZING_TIP_ONLY` (`clearance_deg < 0.25°`), only a tiny summit tip peeks over `highest_intervening_ridge`; never assign a distant peak's name ($D > 5\text{ mi}$) to the closer ridge ($D < 3\text{ mi}$) below it.
     - **Camera Pitch, Foreground Decoupling ($D < 200\text{ m}$) & Midground Ordering ($D \ge 300\text{ m}$)**: Wide-angle downward pitch ($-5^\circ$ to $-12^\circ$) shifts the $0^\circ$ horizon into the upper third of the frame; measure vertical angle **differences** ($\Delta\text{alt}$) and anchor `calibrate-azimuth` near the horizon. Never lock distant peak azimuths ($D > 2\text{ km}$) to a close foreground feature ($D < 200\text{ m}$). Compact midground landmarks ($D \ge 300\text{ m}$) must sit on the matching side of `center_azimuth_deg` (`calibrate-azimuth --anchor <bearing>:<x>`). For elongated lakes ($>300\text{ m}$) or nearby tarns/switchbacks, verify shoreline shape and camera position on `render-topo --radius-km 1.5`.
     - **Unnamed Horizon Peaks (`dem_horizon_highpoints`), Occlusion & Negative Evidence**:
       - If an `Unnamed Horizon Highpoint` sits at the exact bearing and vertical angle of a visible peak while the nearest named summit is off-axis or `OCCLUDED`, identify it by its elevation and `nearest_named_feature` ridge context (e.g., "Peak 11,062 ft / 3,372 m").
       - Verify `line_of_sight` (`VISIBLE` vs. `OCCLUDED`; if `UNKNOWN (INCOMPLETE_ELEVATION_DATA)`, do not assume `VISIBLE`). Background features behind an intervening ridge MUST have $D_{\text{background}} > D_{\text{foreground}}$ and higher vertical angle.
       - **Point Summits (`POINT_SUMMIT`) vs. Extended Landforms (`EXTENDED_LANDFORM_CENTROID`)**: If a `POINT_SUMMIT` is `OCCLUDED` by a separate closer ridge ($D_{\text{occluder}} < 0.80 \times D_{\text{target}}$), omit it even if mentioned in `post.md` or `--ref-elev-ft`. When a plateau rim, lake shoreline, canyon wall, or steep mountain shoulder ($D_{\text{occluder}} \approx D_{\text{target}}$) blocks its own setback summit/centroid, verify via `render-pano` and `render-topo` and describe the visible part accurately (e.g., "the steep west shoulder of Mount X, whose true summit is foreshortened out of sight behind the upper cliff rim").
       - **Negative-Evidence Check & No Silent Omission**: Reject any candidate bearing where `horizon-skyline` reports a towering `VISIBLE` peak (`Alt > 6°` with large positive clearance) at `x` but the photo shows **open, unobstructed sky** (not blocked by trees, clouds, or downward pitch). Every distinct horizon unit cropped in the photo must be accounted for in `<photo_stem>_pano.png`.
   - **Batched Native-Resolution Cropping (Always Pass `--grid` After `extract_metadata.py`)**: Wait until after reading `extract_metadata.py` and viewing the photo before running `crop_photo.py` so all needed `--box X1 Y1 X2 Y2:label` or `--all-slices` operations can be batched into a single call (optionally with `--overview 2000 --grid`). Always include `--grid` on both initial and any follow-up `crop_photo.py` calls, and view every crop you generate.

4. **Primary Subject & Setting**:
   - Identify the compositional focal point (summit reflection, alpine basin, geological formation, waterfall, wildflower) and characterize lighting and terrain environment.

5. **Fauna & Flora Identification**:
   - Use `crop_photo.py` to inspect diagnostic details on close-up plants, fungi, lichens, insects, or wildlife. Never describe close-up botanical traits (needle cluster counts, bark fissures, bract scales, leaf pubescence) unless visually resolved in a dedicated high-resolution crop.
   - **Visual-Phenotype & Life-Stage Consistency Check**: Verify that every visual trait implied by a common or scientific name is visibly present. If a specimen is in a juvenile stage lacking adult markers or cannot be macroscopically separated from a sympatric sister species, explicitly state the life stage and sister-species ambiguity rather than asserting an unqualified species name. Be wary of cast shadows or angles mimicking missing wings or altered colors.

---

## Augmentation & Editorial Constraints

- **Plain-Text Dual Units Everywhere**:
  - Format every elevation, distance, and temperature in plain-text dual units: "X,XXX ft / X,XXX m", "~X.X mi / X.X km" (with dual units on both bounds of any range: "~0.5 mi / 0.8 km to 3.9 mi / 6.3 km"), and "XX°F / YY°C"—including bold feature headers (at most one elevation per header), unnamed peaks, and flora/fauna habitat ranges.
  - Never use bare single units, spelled-out single units ("twenty miles"), parentheses "ft (m)", markdown code backticks around measurements, or em-dashes (`—`; always use spaced en-dashes ` – `).
- **Ban on Angle Degree Symbols (`°`) & Internal Image Coordinates (`x = 0.XX`)**:
  - Use `°` **only** in DMS GPS coordinates and "°F / °C" temperatures—never for compass bearings, vertical angles, or slope inclinations.
  - Never include normalized image coordinates (`x = 0.35`, `x ≈ 0.48`), pixel coordinates, or internal JSON field names (`projected_x_fraction`) in any section; use natural compositional prose ("on the upper right skyline") with cardinal directions and dual-unit distances.
- **Concise Bullets & Ban on Unverified Lore from LLM Memory**:
  - Augment identified features with 2 – 3 concise bullets max per feature. Never guess or assert specific historical naming origins/eponyms, pioneer dates, first-ascent dates, climber attributions, or taxonomic author/year citations from unverified LLM memory unless stated in `post.md` or verified via `search_web`; otherwise ground bullets in verified physical geography, watershed connections, geology, ecology, or trail context.
- **Orchestration Boundary & Single-Turn Final Delivery**:
  - **Single-Writer Rule (`catalog.md` Off-Limits) & Subagent Isolation**: Never read, grep, or modify `<date>/catalog.md`. Never list or read other photos' files in `scratch/`, and never re-read `scratch/<photo_stem>_entry.md` after writing it.
  - **Single-Turn `write_file` + Compact `send_message` Delivery**: Write your complete markdown entry to `<workspace>/scratch/<photo_stem>_entry.md` (`Overwrite: true`) **and** invoke `send_message` to the parent agent **in the same tool-call turn**. Transmit only a compact completion summary in `send_message`: (1) exact absolute path `<workspace>/scratch/<photo_stem>_entry.md`, (2) 1-line `Location`, `Suggested Caption`, and `Suggested Draft Placement` summary, and (3) any draft discrepancy note. Because `send_message` immediately wakes the parent to run `catalog_tools.py upsert`, your turn after `send_message` MUST contain **zero tool calls**.

---

## Scripting & Computation Guidelines

- **Use Packaged Tooling First & Exact Skill Script Paths**: Always invoke `extract_metadata.py`, `crop_photo.py`, and `geo_tools.py` via `C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\<script>.py`—never `<workspace>\scripts\...`, and never read `geo_tools.py` source code to import internal functions.
- **Ban Custom JSON-Parsing Scripts & Raw `*_skyline.json` / `*_nearby.json` Reads**: Never write custom `.py` scripts, `python -c` one-liners, or PowerShell `ConvertFrom-Json` commands to parse `<photo_stem>_nearby.json` or `<photo_stem>_skyline.json`, and never call `read_file` on either JSON file. Instead, query via `geo_tools.py nearby-features` CLI filter flags (`--types Summit,Peak,Ridge,Gap,Valley,Basin,Flat,Lake,Cliff,Pillar,PopulatedPlace`, `--az-min`, `--az-max`, `--max-dist-mi`, `--search "<term1,term2>"`, `--max-results 35`) or `horizon-skyline --check-coords "Name:lat,lon"` (always passing `--out-json`). All radii in `25.1–43.0 km` share the `40 km` `%TEMP%` cache bucket, so never escalate `--radius-km` above `40.0`.
- **No Redundant `elevation-profile` or Sector `nearby-features` Calls**: Do not run `geo_tools.py elevation-profile` or repeat sector `nearby-features` queries on peaks already evaluated in `horizon-skyline`'s `visible_features_in_fov` or `occluded_features_in_fov`.
- **No `&&` or `;` Chaining Across Skill Scripts (Issue Parallel `run_command` Calls)**: Windows PowerShell 5.1 rejects `&&` with `ParserError`. Never use `&&`, and do **not** chain multiple `geo_tools.py` or `crop_photo.py` invocations with `;` inside a single `run_command` call (which executes sequentially past the `10,000 ms` timeout and truncates combined `stdout`). Issue independent commands as **separate parallel `run_command` tool calls in the same turn**.
- **Sandbox Bypass (`BypassSandbox: true`) & Wait Timeout (`WaitMsBeforeAsync: 10000`)**: Always pass `BypassSandbox: true` and `WaitMsBeforeAsync: 10000` on the first attempt for **every** `python` skill script call on Windows. If a command moves to the background (`task-XX`), **never** call `manage_task`, read `task-XX.log`, call `schedule`, or read the output file before receiving the `<SYSTEM_MESSAGE>` completion notification; if no independent calls remain, end your turn with a 1-sentence visible text message and zero tool calls.
- **Initial Pass Budget & No Custom Vision/Math Scripts**: Budget at most **1–2 `horizon-skyline --out-pano` renders** and **1–3 batched `crop_photo.py` commands** during your initial pass. Never write custom Python scripts using RGB thresholding, NumPy/SciPy gradients, `PIL`/`cv2` cropping/rulers, or custom Haversine/projection math, and never hardcode landmark coordinates from LLM memory or query Wikipedia. Visually inspect every crop and synthetic panorama in `scratch/` with `read_file`.
- **Anti-Stub, No Transcript Scraping & Photo-Prefixed `scratch/` Files**: Never create unexecuted comment-only `.py` files or read `.system_generated/logs/transcript*.jsonl` / `.system_generated/steps/<N>/output.txt`. Prefix every file in `scratch/` with `<photo_stem>_` (e.g., `scratch/281A5985_topo.png`, `scratch/281A5985_entry.md`). Do unit conversions in mental math, never run inline `python -c` with nested quotes, and use backtick (`` ` ``) escaping in PowerShell.

---

## Output Format

In a **single tool-call turn**, write the completed analysis to `<workspace>/scratch/<photo_stem>_entry.md` (`Overwrite: true`) and call `send_message` to the parent agent with a compact completion summary per `## Augmentation & Editorial Constraints` above, then end your final turn with a 1-sentence confirmation and **zero tool calls**.

**Strict 4-Bullet Metadata Header**: Under `### Photo: \`<filename>\``, include **only** the 4 canonical bullets (`Date & Time Taken`, `GPS Coordinates`, `Elevation`, `Location`)—never add `- **Camera & Exposure**:` or `- **Camera Orientation & Solar Geometry**:` bullets.

```markdown
### Photo: `<filename>`
- **Date & Time Taken**: [e.g. September 10, 2026 at 08:49:07 PDT]
- **GPS Coordinates**: [coordinates in DMS format, e.g. 45°13'56.95" N 117°21'37.91" W]
- **Elevation**: [XXX ft / YYY m, plus relative elevation context if applicable, e.g. ~1,080 ft / 329 m above trailhead]
- **Location**: [Precise geographic location, specific trail name and number, wilderness, county, state]

### Identified Natural Features

- **[Feature Name (single authoritative dual-unit elevation, e.g., Peak 12,275 ft / 3,741 m or Granite Peak (12,840 ft / 3,914 m))]**:
  - [Visual description, plain-text dual-unit distance (~X.X mi / Y.Y km) and cardinal direction from camera (zero `°` angle symbols and zero `x = 0.XX` coordinates), plus geological/geographic context]
  - [Verified factual details capped at 2 – 3 concise bullets, using consistent benchmark or DEM-reconciled dual-unit elevations and zero unverified eponyms, taxonomic citations, or first-ascent dates from LLM memory]

### Identified Fauna & Flora

- **[Common Name (*Scientific Name*)]**:
  - [Visual morphology observed from crop/photo, habitat context (with plain-text dual units on any elevation bounds, e.g., between 9,500 ft / 2,896 m and 11,500 ft / 3,505 m), and distinguishing characteristics]

### Suggested Caption

[Caption <= 3 sentences describing the focal subject and setting using cardinal directions (zero `°` angle symbols, zero code backticks around measurements, and plain-text dual units X,XXX ft / X,XXX m or ~X.X mi / Y.Y km). Conclude every caption with its DMS GPS coordinates in parentheses, e.g. (45°13'56.95" N 117°21'37.91" W)]

### Suggested Draft Placement

[Suggest which section or narrative beat of `post.md` this photo best illustrates. Cite the narrative prose paragraph number, exact line number, and a short verbatim quote snippet from `post.md` (counting **only narrative prose paragraphs after the opening `----------` metadata separator**, excluding title, `Start:`/`End:` coords, hike stats header, horizontal rules, headings, and standalone image/caption lines—read line and paragraph numbers directly from `read_file` of `post.md` and never write custom paragraph-counting scripts). Explicitly flag any `post.md` landmark that is geometrically out-of-frame/`OCCLUDED` or any `post.md` / `--ref-elev-ft` elevation discrepancy.]
```
