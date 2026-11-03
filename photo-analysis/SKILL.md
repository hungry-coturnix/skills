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

This skill includes pre-packaged helper utilities in `scripts/`. All CLI flags and output JSON schemas are documented below so you **never** need to run `--help` or inspect script source code:

- **`scripts/extract_metadata.py`**: Runs ExifTool (with directory-level batch caching), queries official USGS 3DEP ground elevation and administrative boundaries, detects stitched panoramas, and computes chronologically preceding/succeeding hike travel vectors in one shot:
  ```bash
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\extract_metadata.py "<photo_path>" --ref-elev-ft "Trailhead:5267" --ref-elev-ft "High Point:7990"
  ```
  - **CLI Flags**: `<photo_path>`, `--ref-elev-ft "Label:ElevFt"` (repeatable).
  - **Output JSON Keys**:
    - Core & coordinates: `photo_path`, `date_time`, `gps_dms`, `lat`, `lon`
    - Elevation: `ground_elevation_ft`, `ground_elevation_m`, `exif_elevation_ft`, `exif_elevation_m`, `elevation_formatted`, `elevation_deltas`
    - Camera, lens & exposure: `camera_model`, `lens_model`, `focal_length_mm`, `focal_length_35mm`, `estimated_single_frame_hfov_deg`, `exposure_time`, `f_number`, `iso`, `camera_spec_formatted`, `image_width`, `image_height`
    - Panorama detection: `is_stitched_panorama_candidate`, `panorama_guidance`
    - Neighbor & hike trajectory vectors: `prev_photo`, `next_photo` (automatically skipping stationary burst/panorama source frames `<4.0 m`; each includes `filename`, `date_time`, `lat`, `lon`, `elevation_ft`, `elevation_m`, `elevation_change_ft`, `elevation_change_m`, noise-aware `trail_gradient`, `elapsed_time_min`, and `travel_vector_from_prev` / `travel_vector_to_next`), `prev_hike_stops`, `next_hike_stops` (up to 3 distinct preceding and 3 distinct succeeding hike stops separated by `>= 25 m` or `>= 45 s`)
    - Administrative boundaries: `admin_boundaries` (resolved County, State, and overlapping Federal Land units: National Park, Wilderness Area, National Forest)
  - Do **not** run `exiftool.exe` separately after running `extract_metadata.py`, as all camera, lens, exposure, dimension, and neighbor travel-vector fields are already included.
  - *Fallback command*: Only if `extract_metadata.py` cannot be run, extract display DMS, calculation decimals, and altitude in one call:
    ```bash
    exiftool.exe -d "%Y-%m-%d %H:%M:%S" -DateTimeOriginal -GPSPosition -c "%d°%d'%.2f\"" -GPSLatitude# -GPSLongitude# -GPSAltitude# -FocalLength# -FocalLengthIn35mmFormat# "<photo_path>"
    ```

- **`scripts/crop_photo.py`**: Crops high-resolution images (or paletted/RGBA images, automatically converted to RGB) to inspect fine details (botanical features, distant peaks, foreground shadows, trail junctions, lake shapes) without downsampling artifacts:
  ```bash
  # Predefined regions: center, top-left, top-right, bottom-left, bottom-right, left, right
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --region center --out "scratch/<photo_stem>_center.jpg"
  # Single horizontal panorama slice (e.g. slice 2 of 4) or all N slices in one command with coordinate grid
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --slice 2 4 --out "scratch/<photo_stem>_s2.jpg"
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --all-slices 3 --grid --out "scratch/<photo_stem>_slice.jpg"
  # Bounding box in fractions 0.0 - 1.0 or pixels (X1 Y1 X2 Y2[:label]), repeatable in a single call
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --box 0.2 0.3 0.6 0.7:peak --box 0.4 0.6 0.8 0.9:shadow --grid --out "scratch/<photo_stem>_crop.jpg"
  # Downscaled full-frame overview with outer-margin fractional/pixel coordinate grid
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\crop_photo.py "<photo_path>" --overview --max-dim 2000 --grid --out "scratch/<photo_stem>_overview.jpg"
  ```
  - **CLI Flags**:
    - `--region {center,top-left,top-right,bottom-left,bottom-right,left,right}`
    - `--slice K N` (extract slice `K` of `N` horizontal panorama slices)
    - `--all-slices N` (alias `--slices N`: extract all `1..N` horizontal slices in one invocation)
    - `--box X1 Y1 X2 Y2[:label]` (repeatable; accepts fractional `0.0–1.0` or pixel coordinates and optional `:label` tag)
    - `--grid` (draws subtle 1px dashed coordinate gridlines every 5%/10% and renders full-image fractional `x=0.05..0.95`, `y=0.05..0.95` and pixel `x_px`, `y_px` labels inside a `36 px` white outer border collar so text never occludes image details)
    - `--overview [MAX_DIM]` and `--max-dim MAX_DIM` (default `2000 px`)
    - `--out <path>` (when multiple slices/boxes are generated, the slice/box label is automatically appended before the file extension)

- **`scripts/geo_tools.py`**: Unified geographic, 3DEP DEM horizon skyline, line-of-sight, azimuth calibration, topo map rendering, and solar analysis utility:
  ```bash
  # Distance, bearing, and cardinal direction between two GPS coordinates:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py distance <lat1> <lon1> <lat2> <lon2>

  # Solar elevation, solar azimuth, 3D shadow azimuth, and 2D image-plane shadow clock lookup table:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py sun-position <lat> <lon> "YYYY-MM-DD HH:MM:SS" [--center-az <est_azimuth>] [--out-json "scratch/<photo_stem>_sun.json"]

  # Query named summits, ridges, lakes, streams, valleys, and up to 5 closest unnamed water bodies (<=1.5 km) within radius (default 12 km):
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py nearby-features <lat> <lon> --radius-km 15.0 [--verbose] [--out-json "scratch/<photo_stem>_nearby.json"]

  # Compute 3DEP DEM topographic horizon (including UNNAMED peaks/ridges and 50m-500m foreground ridges), named summit visibility up to 22+ km, FOV x-fraction projections, AND render an annotated 2D synthetic DEM panorama:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py horizon-skyline <lat> <lon> --radius-km 22.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> [--az-min <deg>] [--az-max <deg>] [--projection {rectilinear,cylindrical}] [--check-coords "Name:lat,lon"] [--image-width <px>] --out-pano "scratch/<photo_stem>_pano.png" --out-json "scratch/<photo_stem>_skyline.json"

  # Or render a standalone 2D front-to-back ray-marched synthetic DEM panorama (with azimuth/altitude grid, sub-2km canyon distance bands, meadow coloring, FOV bounds, and peak labels):
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py render-pano <lat> <lon> --out "scratch/<photo_stem>_pano.png" [--center-az <est_azimuth>] [--hfov-deg <hfov_deg>] [--az-min <deg>] [--az-max <deg>] [--alt-min <deg>] [--alt-max <deg>] [--radius-km 22.0] [--out-json "scratch/<photo_stem>_pano.json"]

  # Download and annotate a USGS Topo map with camera marker, optional FOV wedge, bearing rays, and nearby summits:
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png" --radius-km 5.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> [--rays <b1,b2,...>] [--out-json "scratch/<photo_stem>_topo.json"]

  # Topographic elevation profile & line-of-sight occlusion check between camera and candidate target (40-step 3DEP DEM profile):
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py elevation-profile <cam_lat> <cam_lon> <target_lat> <target_lon> [--samples 40 | --steps 40] [--full-profile] [--out-json "scratch/<photo_stem>_prof.json"]

  # Invert lens projection to solve exact camera center_az, left_edge_deg, and right_edge_deg from a known landmark's bearing and horizontal x_fraction (0.0-1.0):
  python C:\Users\Tilman\.gemini\config\skills\photo-analysis\scripts\geo_tools.py calibrate-azimuth --hfov-deg <hfov_deg> --anchor <bearing_deg>:<x_fraction> [--projection {rectilinear,cylindrical}]
  ```
  - **Complete `geo_tools.py` Subcommand Flags & Output JSON Keys**:
    - `sun-position`:
      - Flags: `<lat> <lon> "YYYY-MM-DD HH:MM:SS"`, `--center-az <deg>`, `--out-json <path>`.
      - Output JSON keys: `solar_azimuth_deg`, `solar_altitude_deg`, `shadow_azimuth_deg`, `flat_ground_2d_shadow_by_camera_octant` (8-octant 2D image-plane shadow lookup table, including `12/3/6/9 o'clock` and diagonal `10-11 o'clock` vs. `7-8 o'clock`), `observed_2d_shadow_clock_to_camera_bearing`, `terrain_slope_caveat`.
    - `nearby-features`:
      - Flags: `<lat> <lon>`, `--radius-km <km>`, `--verbose` (includes distant `Stream` mouths `>5 km`), `--out-json <path>`.
      - Output JSON keys: compact 1-line feature dicts (`name`, `type`, `lat`, `lon`, `elevation_ft`, `elevation_m`, `distance_mi`, `distance_km`, `bearing_deg`, `cardinal`, `source`), including up to 5 closest unnamed water bodies within `1.5 km` (`Unnamed Tarn / Water Body (lat, lon)`).
    - `horizon-skyline`:
      - Flags: `--radius-km`, `--center-az`, `--hfov-deg`, `--az-min`, `--az-max`, `--projection {rectilinear,cylindrical}` (auto-selects `cylindrical` when `--hfov-deg > 100` or when explicit sector bounds `--az-min`/`--az-max` span `< 360°`), `--check-coords "Name:lat,lon"` (repeatable), `--image-width <px>`, `--out-pano <path>`, `--out-json <path>` (alias `--json-out`).
      - Top-level JSON keys: `camera`, `fov`, `visible_features_in_fov`, `occluded_features_in_fov`, `top_dominant_360_visible_summits`, `foreground_ridges` (`50m–500m` near-field local maxima), `dem_horizon_highpoints` (`>=500m`), `named_features_visibility`, `synthetic_pano`. Each feature entry includes `bearing_deg`, `distance_mi`, `distance_km`, `elevation_ft`, `elevation_m`, `target_vertical_angle_deg`, `max_intervening_angle_deg`, `clearance_deg`, `line_of_sight` (`"VISIBLE"`, `"OCCLUDED"`, or `"CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"`), `visibility_class` (including `"GRAZING_TIP_ONLY"` when `0.0 <= clearance_deg < 0.25`), `visibility_note`, `geometry_type` (`"POINT_SUMMIT"` vs. `"EXTENDED_LANDFORM_CENTROID"`), `in_fov`, `projected_x_fraction`, and `highest_intervening_ridge` / `occluded_by`.
    - `elevation-profile`:
      - Flags: `<cam_lat> <cam_lon> <target_lat> <target_lon>`, `--samples` (alias `--steps`), `--full-profile` (includes the 41-step `profile` array, omitted by default for compact output), `--out-json <path>`.
      - Top-level JSON keys: `distance_mi` / `total_distance_mi`, `distance_km` / `total_distance_km`, `bearing_deg`, `cardinal`, `camera_elevation_ft`, `target_elevation_ft`, `target_vertical_angle_deg`, `max_intervening_angle_deg`, `clearance_deg`, `line_of_sight`, `visibility_class`, `visibility_note`, `highest_intervening_ridge`, `occluded_by` / `occluding_ridge`.
    - `render-pano`:
      - Flags: `--out`, `--center-az`, `--hfov-deg`, `--az-min`, `--az-max`, `--alt-min`, `--alt-max`, `--radius-km`, `--out-json`. Also outputs `visible_features_in_fov` and `dem_horizon_highpoints` in stdout JSON.
    - `render-topo`:
      - Flags: `--out` (or optional positional `out_path`), `--radius-km`, `--center-az` (alias `--azimuth`), `--hfov-deg` (alias `--fov`), `--rays`, `--out-json`.
    - `calibrate-azimuth`:
      - Flags: `--hfov-deg`, `--anchor <bearing_deg>:<x_fraction>`, `--projection {rectilinear,cylindrical}`.

---

## Analysis Steps

1. **Location, Date & Time**:
   - Extract the timestamp, GPS coordinates, and `admin_boundaries` (County, State, National Park, Wilderness Area, National Forest) using `extract_metadata.py`.
   - Identify the exact geographic location: wilderness area, national forest, state/national park, mountain range, and specific trail.
   - **Trail Junction Check**: When photos are taken near trail intersections, do not simply snap to the trailhead trail name. Check whether the route has branched onto a secondary trail.
   - If EXIF or GPS data is missing, note `[Not available in EXIF]` and estimate from visual and chronological context.

2. **Elevation**:
   - Camera GPS altitude is often ellipsoidal or barometric and can deviate from actual ground level by 50 – 150 ft.
   - Use `extract_metadata.py` (with optional `--ref-elev-ft "Label:ElevFt"` flags) to retrieve official ground elevation from USGS 3DEP and pre-computed benchmark deltas.
   - Always report elevation in dual units: `XXX ft / YYY m`. Include relative elevation context whenever relevant (e.g. `~XXX ft / YYY m above trailhead; ~YYY ft / ZZZ m below summit`).

3. **Features & Line-of-Sight Verification**:
   - Identify specific visible natural features: peaks, ridges, cirques, lakes, tarns, saddles, and meadows.
   - **Two-Stage Geometry-First Ordering (Anti-Narrative-Anchoring Rule)**:
     - **Stage 1 (Unbiased Physical & Geometric Discovery — Before Reading `post.md`)**: First run `extract_metadata.py`, view the photo (`read_file`), run `sun-position` and `nearby-features`, and run your initial `horizon-skyline --out-pano` / `render-topo` pass based purely on visual terrain, solar shadows, and travel vectors. Do **NOT** read `post.md` during Stage 1, as reading directional prose first causes severe narrative confirmation bias.
     - **Stage 2 (Narrative Cross-Check & Draft Placement — Mandatory `post.md` Read)**: Once your initial geometric orientation is established, you **MUST** read `post.md` to confirm specific trail/junction context, test any landmarks mentioned near that hike segment (via `horizon-skyline` or `elevation-profile`), and determine the exact `Suggested Draft Placement`.
     - **Physical Geometry Always Wins**: Use `post.md` for hike context and draft placement, but **never** assume a photo points in the same direction as a nearby sentence in `post.md` (e.g., if `post.md` says *"From the saddle, the view opened up to the east"*, the hiker may still have photographed a northwest or look-back vista from that saddle). Physical geometry (solar shadows, travel vector, and 3DEP DEM skyline profiles) **always** overrides directional prose in `post.md`.
   - **Omnidirectional Feature Discovery & `VISIBLE` vs. `in_fov` Distinction**:
     - Always run `geo_tools.py nearby-features <lat> <lon>` before testing individual peaks. It returns all named summits, ridges, lakes, streams, valleys, and nearby unnamed water bodies (`<=1.5 km`) in 360° sorted by distance with their bearings and elevations. Do not restrict queries, sightlines, or ray-tracing to a single preconceived quadrant.
     - Note that `line_of_sight: "VISIBLE"` is an azimuth-independent physical property from `(lat, lon)`, whereas `in_fov: false` depends entirely on your `--center-az` argument. Always inspect `top_dominant_360_visible_summits` in `horizon-skyline` (and any peak named in `post.md` that has `line_of_sight: "VISIBLE"`): never dismiss a visible peak solely because `in_fov: false` under your initial `--center-az` guess—test a `horizon-skyline` centered on that peak's bearing and compare the 2D skyline silhouette and shadow alignment.
   - **Mandatory Travel-Vector Framing, Switchback Chords & Chronological Landmark Bearings**:
     - Inspect the `prev_photo`, `next_photo`, `prev_hike_stops`, and `next_hike_stops` walking vectors and noise-aware `trail_gradient` fields automatically returned by `extract_metadata.py` (or compute between arbitrary coordinates via `geo_tools.py distance <prev_lat> <prev_lon> <lat> <lon>`).
     - Most trail and ridgeline photos are captured along natural axes. Prioritize candidate camera orientations in this order:
       1. **Forward** along the travel vector ($\text{bearing} \pm 45^\circ$).
       2. **Lateral** vistas looking off the trail or ridgeline ($\text{bearing} \pm 90^\circ$).
       3. **Look-backs** along the reverse path ($\text{bearing} + 180^\circ \pm 45^\circ$).
     - **Local Trail Switchback vs. Macro GPS Chord & Divergent Vectors**: `prev_photo` and `next_photo` (and `prev_hike_stops` / `next_hike_stops`) measure straight-line chords between photo locations. On switchbacks or traverses, the local footpath visible in the foreground can point $60^\circ–180^\circ$ away from the macro GPS chord. When `prev_photo` and `next_photo` bearings diverge, or when a high-prominence `VISIBLE` massif in `top_dominant_360_visible_summits` lies $30^\circ–90^\circ$ off the macro chord, test both vectors and the off-trail vista.
     - **Chronological Landmark Bearing Rule**: Always compare a landmark's bearing in `nearby-features` against your walking vector bearing and elevation delta (`prev_photo` / `next_photo` / `prev_hike_stops`). If you are traveling `343° NNW` (descending) and a landmark (e.g., Bighorn Plateau) lies at `158° SSE` (`~180°` behind your travel vector), you have **already passed** that landmark—do not describe the photo as "approaching" or "ascending toward" it.
   - **Rigorous Solar Shadow Verification**:
     - Calculate solar azimuth, 3D shadow azimuth (`shadow_azimuth = (solar_azimuth + 180) % 360`), and the 2D image-plane shadow lookup table using `geo_tools.py sun-position <lat> <lon> "<timestamp>" [--center-az <est_azimuth>]`.
     - **Targeted Foreground Shadow Inspection**: When candidate camera orientations conflict or shadow direction is subtle in the full-frame view (and isolated vertical shadow casters such as trees, shrubs, or rocks stand on relatively even ground), use `crop_photo.py` to inspect a foreground shadow patch and cross-check the observed shadow vector against `sun-position` before locking in `--center-az`.
     - **Explicit 2D-to-3D Shadow Azimuth Math**: Use the `sun-position` 2D shadow lookup table to cross-check camera bearing from flat-ground shadow directions in the image frame (`camera_azimuth ≈ (shadow_azimuth - image_shadow_clock_offset) % 360`, where `0° = 12 o'clock / straight away into scene`, `+90° = 3 o'clock / left-to-right`, `180° = 6 o'clock / straight toward camera`, `-90° (270°) = 9 o'clock / right-to-left`), alongside the relative sun angle $\Delta\text{az} = (\text{solar\_azimuth} - \text{camera\_azimuth}) \bmod 360^\circ$:
       - Shadows pointing **straight away/forward** (`12 o'clock`, `0°` in frame) $\rightarrow$ `camera_azimuth ≈ shadow_azimuth` ($\Delta\text{az} \approx 180^\circ$, $150^\circ – 210^\circ$, sun behind camera).
       - Shadows pointing **left-to-right** (`3 o'clock`, `+90°` in frame) $\rightarrow$ `camera_azimuth ≈ (shadow_azimuth - 90) % 360` ($\Delta\text{az} \approx 270^\circ$, $240^\circ – 300^\circ$, sun on left flank).
       - Shadows pointing **straight toward the camera/bottom** (`6 o'clock`, `180°` in frame) $\rightarrow$ `camera_azimuth ≈ solar_azimuth` ($\Delta\text{az} \approx 0^\circ$, $330^\circ – 30^\circ$, sun in front of camera; scene is backlit/prone to glare). **Backlit Morning/Evening Check**: When solar altitude is low ($<25^\circ$) and the camera points within $\pm 30^\circ$ of the sun azimuth, tree trunks and boulders facing the camera will have **dark shadowed front faces with bright rim-lit edges**, and ground shadows will point *toward* the bottom of the frame. Do not confuse into-the-sun rim lighting with cross-lighting!
       - Shadows pointing **right-to-left** (`9 o'clock`, `-90°` in frame) $\rightarrow$ `camera_azimuth ≈ (shadow_azimuth + 90) % 360` ($\Delta\text{az} \approx 90^\circ$, $60^\circ – 120^\circ$, sun on right flank).
       - **Critical Diagonal Distinction (Up-Left vs. Down-Left)**: Carefully distinguish shadows pointing **away and left** (`10–11 o'clock`, into the scene: sun is **behind-right** at $\Delta\text{az} \in [110^\circ, 150^\circ]$) from shadows falling **toward the camera and left** (`7–8 o'clock`, downward into the lower-left foreground while camera-facing front rock/trunk surfaces are dark/shadowed and rear/right edges facing the sun are rim-lit: sun is **in front-right** at $\Delta\text{az} \in [15^\circ, 75^\circ]$). Do not confuse shadows falling toward the lower-left foreground with pure `-90°` cross-lighting or behind-right lighting!
       - **Hillside Slope & Wide-Angle Perspective Caveat**: On inclined mountain slopes ($20^\circ–35^\circ$) or near the lower corners of a wide-angle lens (24–35mm), ground slope and perspective foreshortening can rotate apparent 2D shadow angles by $\pm 15^\circ–35^\circ$. Treat 2D shadow clock angles as a $\pm 35^\circ$ octant consistency constraint—never as a $1^\circ$ protractor that overrides an exact multi-peak 3DEP DEM skyline match.
     - Shadows are a physical constraint: never rationalize shadow directions to fit an assumed landmark.
   - **Horizon Skyline, Synthetic DEM Panorama, Panorama/Canyon Workflows & Geometric Guardrails**:
     - Whenever mountain ridges, peaks, meadows/basins, or canyon walls are visible, run `geo_tools.py horizon-skyline <lat> <lon> --radius-km 22.0 --center-az <est_azimuth> --hfov-deg <hfov_deg> --out-pano "scratch/<photo_stem>_pano.png" --out-json "scratch/<photo_stem>_skyline.json"` (using `estimated_single_frame_hfov_deg` from `extract_metadata.py`, e.g., `54.4` for a 35mm lens).
     - **Stitched Panorama (`-Pano.jpg`) Workflow**: When `is_stitched_panorama_candidate: true` (filename ending in `-Pano.jpg` or multi-frame wide stitch), `estimated_single_frame_hfov_deg` does NOT represent the stitched horizontal span. Run a single wide `horizon-skyline --az-min <min> --az-max <max> --projection cylindrical --out-pano "scratch/<photo_stem>_pano.png" --out-json "scratch/<photo_stem>_skyline.json"` and use `crop_photo.py --all-slices 3 --grid` (or `--overview --grid`)—never 7 overlapping rectilinear synthetic panoramas or custom pixel-ruler scripts.
     - **Steep Canyon / Uncertain Azimuth (`render-pano --az-min 0 --az-max 360`) Workflow**: Inside steep V-shaped canyons/gorges where local canyon walls (`0.3–1.5 km` away) dominate the frame or when camera azimuth is uncertain, render a single `--az-min 0 --az-max 360` DEM panorama (`render-pano`) to match the near-field canyon wall distance bands (`<0.5 km` deep pine green, `0.5–1 km` forest green, `1–2 km` olive-slate) and V-notch geometry.
     - **Visual Alignment with Synthetic DEM Panorama (`render-pano` / `--out-pano`)**: View the generated `scratch/<photo_stem>_pano.png` with `read_file` alongside the photo. It renders a front-to-back 2D DEM ray-marched perspective view (`Altitude angle` vs. `Azimuth`) shaded by distance band (`<0.5 km`, `0.5–1 km`, `1–2 km`, `2–6 km` slate-green mid-ground, `6–12 km` slate-blue, `>12 km` pale blue) and highlights flat basins/meadows below the camera in gold, with annotated callouts for visible named features (including `[TIP ONLY +0.XX deg]` warnings) and unnamed DEM horizon highpoints. Use it to lock in the exact camera azimuth (`±1°`) by matching foreground slopes, basin floors, canyon notches, and skyline silhouettes.
     - **2D (`projected_x_fraction` + Net Angular Relief `clearance_deg` & `GRAZING_TIP_ONLY`) Verification**:
       - Check `in_fov` and `projected_x_fraction` (`0.00` = left frame edge, `0.50` = horizontal center, `1.00` = right frame edge) for every candidate peak. A summit near the horizontal center of the image (`projected_x_fraction` $\approx 0.35 – 0.65$) **cannot** be $35^\circ – 40^\circ$ off the camera's center azimuth, and any peak with `in_fov: false` is outside the frame even if mentioned in `post.md`.
       - Always verify both horizontal `projected_x_fraction` AND vertical angular relief above intervening ridges (`clearance_deg = target_vertical_angle_deg - max_distinct_intervening_angle_deg`). In a wide-angle lens (24–35mm, VFOV 38°–53°), the **apparent vertical height of a peak above its intervening ridge** as a fraction of image height $H$ is $\Delta y / H \approx \text{clearance\_deg} / \text{VFOV}$. A peak $>7\text{ miles}$ away with $<1.0^\circ$ of net vertical clearance above a closer ridge spans $<2.5\%$ of image height above that ridge—never a towering peak rising $15\%–30\%$ of image height ($\Delta\text{alt} = +6^\circ\text{ to }+12^\circ$) above its base saddle. When `visibility_class` is `GRAZING_TIP_ONLY` (`clearance_deg < 0.25°`), only a tiny summit tip peeks over the closer ridge (`highest_intervening_ridge` / `Unnamed Horizon Highpoint`); never assign a distant peak's name ($D > 5\text{ mi}$) to the broad foreground/midground ridge ($D < 3\text{ mi}$) below it where individual trees, bushes, or talus boulders are visible.
     - **Camera Pitch Warning**: Never assume camera pitch is $0^\circ$ (level horizon at $y = H/2$) when converting pixel row $y$ to true vertical elevation angle. Wide-angle mountain photos frequently pitch downward ($-5^\circ$ to $-12^\circ$) to capture foreground basins/trails—which shifts the $0^\circ$ horizon and distant $+1^\circ\text{ to }+3^\circ$ peaks high into the **upper third of the frame**—or pitch upward ($+5^\circ$ to $+10^\circ$) to capture towering canyon walls. Always measure vertical angle **differences** ($\Delta\text{alt} = \text{alt}_{\text{peak}} - \text{alt}_{\text{col/ridge}}$) and verify they match vertical pixel separation ($\Delta y \times \text{VFOV} / H$) rather than judging a peak's altitude angle from its absolute $y$ position in the frame.
     - **Foreground Decoupling & Midground Horizontal Ordering Invariant**:
       - **Immediate Foreground Decoupling ($D < 200\text{ m}$)**: Never lock the camera azimuth of distant horizon peaks ($D > 2\text{ km}$) to the GPS-to-map compass bearing of a very close foreground feature ($D < 200\text{ m}$ such as a small tarn, boulder, or trail bend). A 15–25m GPS or trail offset shifts an 80m-away foreground feature's bearing by $10^\circ–20^\circ$ while changing a distant peak's bearing by $<0.2^\circ$.
       - **Midground Horizontal Ordering Invariant ($D \ge 300\text{ m}$)**: If a compact, well-defined midground landmark ($D \ge 300\text{ m}$, such as a small alpine tarn or canyon confluence) has bearing $\theta_{\text{mg}}$ and appears at horizontal fraction $x \in [0, 1]$ in the photo, the camera center azimuth $\theta_{\text{center}}$ must place $\theta_{\text{mg}}$ on the corresponding side of the frame (resolvable via `geo_tools.py calibrate-azimuth --hfov-deg <hfov> --anchor <bearing>:<x>`). For large elongated lakes ($>300\text{ m}$ long), verify which end/bay of the shoreline is in view on `render-topo` rather than assuming the visible water is the lake's geometric centroid.
     - **Check for Unnamed Horizon Peaks & Near-Field Foreground Ridges**: USGS GNIS and OpenStreetMap only list *named* summits. Frequently, the prominent peak or ridge crest in the frame is an **unnamed topographic highpoint** (e.g., `Peak 11,062 ft / 3,372 m`) or a local `50m–500m` rise in `foreground_ridges`. Always inspect `dem_horizon_highpoints` and `foreground_ridges` in `horizon-skyline` (and the blue `Peak ~XXXXft` callouts on `scratch/<photo_stem>_pano.png`): if an `Unnamed Horizon Highpoint` sits at the exact bearing and vertical angle of the mountain in the photo while the nearest named summit is $25^\circ+$ off-axis or `OCCLUDED`, identify it by its elevation and ridge location rather than forcing a misattribution to the named summit.
     - **Front-to-Back Depth & Point-Summit vs. Extended-Landform Occlusion Rule**:
       - Never assume a prominent landmark is visible without verifying line-of-sight (`VISIBLE` vs. `OCCLUDED` in `horizon-skyline` or `geo_tools.py elevation-profile <cam_lat> <cam_lon> <target_lat> <target_lon>`). If `line_of_sight` returns `UNKNOWN (INCOMPLETE_ELEVATION_DATA)`, do not assume `VISIBLE`.
       - For any overlapping features in the frame, a feature visually **behind** an intervening ridge/pinnacle MUST have a strictly greater distance ($D_{\text{background}} > D_{\text{foreground}}$) and a higher vertical angle than the intervening ridge.
       - **Point Summits (`POINT_SUMMIT`)**: When `horizon-skyline` or `elevation-profile` reports `line_of_sight: "OCCLUDED"` for a distant summit where the occluding ridge is a closer, separate ridge ($D_{\text{occluder}} < 0.80 \times D_{\text{target}}$), that summit is hidden behind the closer ridge—never include it as a visible peak in the catalog entry even if `post.md` or `--ref-elev-ft` mentions it.
       - **Foreshortened Massif Shoulders ($D_{\text{occluder}} \approx D_{\text{target}}$) & Extended Landforms (`EXTENDED_LANDFORM_CENTROID`)**: USGS GNIS represents multi-mile valleys at their downstream mouth and plateaus (`Flat`/`Bench`), ridges, basins, and lakes by a single interior centroid point. A canyon bend often blocks the distant mouth of the valley you are standing in, a plateau's near rim blocks its own interior centroid when viewed from below, and a steep mountain's lower cliff/shoulder ($D_{\text{occluder}} \approx D_{\text{target}}$) often hides its true summit cairn set back from the cliff edge. In those cases, use `render-pano` and `render-topo` to verify the visible canyon walls, plateau rim, shoreline, or mountain shoulder, and describe the visible part accurately (e.g., "the steep west shoulder of Mount X, whose true summit is foreshortened out of sight behind the upper cliff rim").
     - **Negative-Evidence Check & No Silent Feature Omission**:
       - **No Missing Towering Peaks in Open Sky**: Check all `VISIBLE` peaks in `horizon-skyline` for your candidate `--center-az`. If the tool reports a towering visible peak (`Alt > 6°` with large positive clearance, e.g., a 13,800 ft summit at `x=0.16`) and the photo shows **open, unobstructed sky** at that `(x, alt)` position (not blocked by foreground trees/foliage absent from the bare-earth DEM, clouds, or downward camera pitch cropping the top of the frame), **reject the candidate bearing immediately**.
       - **No Silent Feature Omission**: Every distinct visual horizon unit cropped in the photo (e.g., a sharp craggy ridge on the left vs. a rounded dome in the center) must be explicitly accounted for in the final `render-pano` verification. Never silently omit a prominent cropped feature from your report because your assumed azimuth sector only contains a flat plateau rim.
     - For lakes, verify both line-of-sight visibility and shoreline shape/orientation against the visible water body (or run `geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png"`).
   - For distant features, crop the region using `crop_photo.py` (with `--grid` when aligning fractional coordinates) before finalizing identification.

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
- **Azimuth vs. Distance (No Degree Headings in Initial or Revised Reports)**:
  - Never include camera azimuth or degree headings in public descriptions or captions. This rule applies equally to initial reports and post-correction revisions: never write `"at bearing 46.1°"` or `"at bearings 32° – 41°"` in `Identified Natural Features` or `Suggested Caption`; use cardinal/intercardinal directions and distances only (e.g., `~4.0 mi / 6.4 km to the northeast`).
  - Do include estimated distance from the camera (e.g., `~0.8 mi / 1.3 km to the southwest`) to give the reader a tangible sense of scale.
- **Concise Bullets**:
  - Augment identified features with concise facts regarding geology, glacial history, ecology, or trail context. Cap augmentation at 2 – 3 bullet points per feature.
- **Orchestration Boundary & Unified Disk Staging**:
  - **Single-Writer Rule (`catalog.md` Strictly Off-Limits)**: Subagents analyzing photos must **NEVER** read, grep, or modify `<date>/catalog.md` under any circumstances. Rely strictly on the canonical `## Output Format` template below.
  - **Disk Staging + Final Response Delivery**: Before finishing your turn (and after any user-requested revision), always write your complete formatted markdown entry to `<workspace>/scratch/<photo_stem>_entry.md` using `write_file` (with an absolute path and `Overwrite: true`), AND output the exact same markdown block as your final response text. Do **not** call `send_message` to the parent agent when completing your turn, as returning your final response automatically delivers the message to the parent orchestrator without duplicating context tokens.

---

## Scripting & Computation Guidelines

- **Use Packaged Tooling First**: Rely on bundled skill scripts (`scripts/extract_metadata.py`, `scripts/crop_photo.py`, `scripts/geo_tools.py`) for metadata extraction, coordinate formatting, elevation resolution, detail zooming, spatial bearings/distances, solar orientation, nearby feature lookups, 3DEP DEM horizon skylines (`horizon-skyline`), azimuth calibration (`calibrate-azimuth`), annotated USGS Topo maps (`render-topo`), and line-of-sight elevation profiles (`elevation-profile`). Do not read `geo_tools.py` source code to import its internal functions into custom scripts.
- **Synchronous Wait Timeout (`WaitMsBeforeAsync: 10000`) & Background Task Protocol**: When invoking `extract_metadata.py`, `crop_photo.py`, or `geo_tools.py` via `run_command`, always set `WaitMsBeforeAsync: 10000` (10 seconds). If a command ever moves to the background (`task-XX`):
  1. **Never** call `manage_task` (`Action="status"` OR `Action="list"`), **never** read `task-XX.log`, **never** call `schedule` to set timers checking on the task, and **never** attempt to `read_file` the output image/JSON before the system delivers the genuine `<SYSTEM_MESSAGE>` notification that `task-XX` has finished.
  2. If you have no other independent tool calls to run while waiting, **end your turn by outputting a brief 1-sentence visible text message** (e.g., `"Waiting for background horizon-skyline task to finish."`) with **zero tool calls**. Never emit an empty response (thinking-only with zero visible text and zero tool calls), as the harness will reject it with a model output error.
- **Initial Analysis Pass Image & Panorama Budget**: To prevent context window exhaustion (>125k tokens) and mid-analysis Context Compaction drops, budget at most **2–3 `horizon-skyline --out-pano` renders** and **3–5 `crop_photo.py` commands** during your initial analysis pass (even for wide stitched panoramas; additional targeted checks are permitted if responding to a follow-up user question).
- **Ban "Blind NumPy Vision" & Pixel-Hunting on Photos/Topo Maps**: Never write custom Python scripts using RGB pixel thresholding (e.g., `B > R` to find water or `B - R < 40` to find skylines, which fail on dark mountain shadows and overhanging pine branches) or 1D NumPy/SciPy column gradients (`np.diff`, `scipy.signal.find_peaks`) to detect peaks or tarns. Whenever you create a crop or synthetic panorama in `scratch/`, you MUST visually inspect it with `read_file`. If an unnamed tarn or pond is visible on `render-topo` or in the photo, use the coordinates returned in `nearby-features`—never write scripts to mask cyan pixels on the topo PNG, query Overpass API directly, or parse raw `.tif` DEM files.
- **Ban Custom Cropping, Ruler-Drawing & Image-Dimension Scripts**: Never write custom `PIL`/`cv2` scripts to check image dimensions (`image_width` and `image_height` are already returned by `extract_metadata.py`), crop photos/topo maps, or draw pixel/degree rulers. Always use `scripts/crop_photo.py` (`--box`, `--all-slices`, `--grid`, `--overview`) or `render-topo --radius-km 1.5`.
- **Ban Hardcoding Peak Coordinates from Memory & Custom Haversine Scripts**: Never hardcode mountain or lake `(lat, lon)` coordinates from LLM memory (which frequently hallucinate longitudes by several kilometers), never query Wikipedia for coordinates, and never write custom Haversine/spherical bearing formulas. Always use the authoritative USGS GNIS / 3DEP / OSM coordinates and distances returned by `geo_tools.py nearby-features`, `horizon-skyline`, or `geo_tools.py distance`. If a distant landmark mentioned in `post.md` lies beyond the default search radius, re-run `nearby-features` or `horizon-skyline` with `--radius-km 35.0` (or pass `--check-coords`) rather than guessing coordinates. If a custom script in `scratch/` ever fails, fix or delete it immediately so it is never re-executed after context compaction.
- **Anti-Stub Rule (No Unexecuted Comment-Only `.py` Scratchpad Files)**: Never use `write_file` as an external thinking scratchpad to create comment-only or placeholder `.py` files in `scratch/` that are never executed. Perform all reasoning in your internal thinking block, and always pass an **absolute path** (`<workspace>/scratch/<photo_stem>_...`) when calling `write_file`.
- **Ban Transcript Scraping (`.system_generated/logs/transcript*.jsonl`)**: Strictly forbidden: Never read or parse `.system_generated/logs/transcript.jsonl` or `transcript_full.jsonl` (neither your own nor the parent conversation's) and never write custom scripts to parse `.system_generated/steps/<N>/output.txt`. Use `--out-json "scratch/<photo_stem>_skyline.json"` to persist full JSON cleanly to disk, and recover state from your `scratch/<photo_stem>_*` files if context compaction occurs.
- **Photo-Prefixed `scratch/` Filenames (Concurrent Subagent Safety)**: Multiple subagents run concurrently in the same workspace. Every image crop, annotated topo map, JSON output, entry markdown file, or diagnostic script saved to `scratch/` **must** be prefixed with the photo stem (e.g., `scratch/281A5985_topo.png`, `scratch/281A5985_crop.jpg`, `scratch/281A5985_entry.md`)—never generic filenames like `scratch/topo_map.png`, `scratch/dem.tif`, or `scratch/download_topo.py`.
- **Mental Math for Standard Conversions**: Perform routine scalar conversions (feet to meters, Fahrenheit to Celsius) directly in internal reasoning rather than spawning one-line Python shells.
- **File-First Execution (No Inline `python -c` with Quotes)**: Never execute Python one-liners containing nested quotes, dictionaries, regex, or multi-line strings via `python -c`. Always write the code to a photo-prefixed `.py` file in `scratch/` using file-creation tools and execute `python <file_path>` instead.
- **Host Shell Escaping (PowerShell)**: The host shell is Windows PowerShell, where the escape character is the backtick (`` ` ``), NOT the backslash (`\`). Avoid passing complex CLI arguments with nested single and double quotes (such as ExifTool format strings) directly on the command line; use packaged scripts or file-based Python scripts instead.
- **USGS Topo Image Mode**: Prefer `geo_tools.py render-topo <lat> <lon> --out "scratch/<photo_stem>_topo.png"` to download and annotate USGS Topo maps automatically. If ever opening a USGS Topo PNG manually with `PIL.ImageDraw`, always call `img = img.convert("RGB")` before drawing, as exported PNGs use an 8-bit color palette (`mode='P'`) that raises `ValueError: cannot allocate more than 256 colors`.
- **Avoid Trivial Print Probes**: Do not spawn Python one-liners solely to echo static variables, test string formatting, or print basic arithmetic. Write cohesive scripts when data retrieval, API queries, or spatial calculations are genuinely needed.
- **Diagnostic Scripts Permitted for Debugging**: Targeted diagnostic prints and schema inspections are fully permitted when troubleshooting API errors, parsing complex track files (TCX/GPX), or diagnosing unexpected data formats.

---

## Output Format

Always write the completed analysis to `<workspace>/scratch/<photo_stem>_entry.md` (`Overwrite: true`) AND return the exact same markdown block in your final response using this canonical template.

**Strict 4-Bullet Metadata Header**: The metadata header under `### Photo: \`<filename>\`` must contain **only** the 4 canonical bullets (`Date & Time Taken`, `GPS Coordinates`, `Elevation`, `Location`)—never add custom `- **Camera & Exposure**:` or `- **Camera Orientation & Solar Geometry**:` bullets (which would leak degree headings into `catalog.md`), and never inspect `catalog.md` to check formatting.

```markdown
### Photo: `<filename>`
- **Date & Time Taken**: [e.g. September 10, 2026 at 08:49:07 PDT]
- **GPS Coordinates**: [coordinates in DMS format, e.g. 45°13'56.95" N 117°21'37.91" W]
- **Elevation**: [XXX ft / YYY m, plus relative elevation context if applicable, e.g. ~1,080 ft / 329 m above trailhead]
- **Location**: [Precise geographic location, specific trail name and number, wilderness, county, state]

### Identified Natural Features

- **[Feature Name]**:
  - [Visual description, distance and cardinal/intercardinal direction from camera (no degree bearings), and geological or geographic context]
  - [Relevant factual details capped at 2 – 3 concise bullets]

### Identified Fauna & Flora

- **[Common Name (*Scientific Name*)]**:
  - [Visual morphology observed from crop/photo, habitat context, and distinguishing characteristics]

### Suggested Caption

[Caption no longer than 3 sentences describing the focal subject and setting (using cardinal directions, no degree headings). Conclude every caption with its GPS coordinates in parentheses, e.g. (45°13'56.95" N 117°21'37.91" W)]

### Suggested Draft Placement

[Suggest which section or narrative beat of `post.md` this photo best illustrates. Cite the non-empty paragraph number, exact line number, and a short verbatim quote snippet from `post.md` so there is zero ambiguity between line numbers and paragraph numbers. If `post.md` names a peak or feature at this photo's location that is geometrically proven to be out-of-frame or `OCCLUDED`, explicitly note the discrepancy here rather than silently eliding it with `...` so the orchestrator can reconcile the draft.]
```
