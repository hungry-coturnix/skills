import sys
import os
import re
import math
import json
import hashlib
import tempfile
import argparse
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime

# Prevent Windows console Unicode encoding errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def format_dms(deg, ref_str, is_lat=True):
    if deg is None:
        return ""
    abs_d = abs(deg)
    d = int(abs_d)
    m = int((abs_d - d) * 60)
    s = (abs_d - d - m / 60) * 3600
    ref_u = str(ref_str).upper() if ref_str else ""
    if is_lat:
        hemi = "S" if "S" in ref_u or deg < 0 else "N"
    else:
        hemi = "W" if "W" in ref_u or deg < 0 else "E"
    return f"{d}°{m}'{s:.2f}\" {hemi}"


def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    dist_m = R * c
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    cardinals = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    cardinal = cardinals[int((bearing + 11.25) / 22.5) % 16]
    return {
        "distance_ft": round(dist_m * 3.28084, 1),
        "distance_m": round(dist_m, 1),
        "distance_mi": round(dist_m / 1609.344, 2),
        "distance_km": round(dist_m / 1000.0, 2),
        "bearing_deg": round(bearing, 1),
        "cardinal": cardinal,
    }


def _import_geo_tools():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        import geo_tools
        return geo_tools
    except Exception:
        return None


def get_exif_batch(paths, cache_dir_key=None):
    """
    Extracts metadata for one or more photo paths in a single ExifTool invocation,
    caching directory-wide results in the system temp folder so concurrent subagents
    do not re-run ExifTool 20 times on the same directory.
    """
    if not paths:
        return []
    cache_path = None
    if cache_dir_key:
        dir_hash = hashlib.md5(cache_dir_key.encode("utf-8")).hexdigest()[:14]
        cache_path = os.path.join(tempfile.gettempdir(), f"photo_analysis_exif_batch_v2_{dir_hash}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as cf:
                    cached = json.load(cf)
                if isinstance(cached, list) and cached:
                    return cached
            except Exception:
                pass

    cmd = [
        "exiftool.exe",
        "-d", "%Y-%m-%d %H:%M:%S",
        "-DateTimeOriginal",
        "-GPSLatitude#",
        "-GPSLatitudeRef",
        "-GPSLongitude#",
        "-GPSLongitudeRef",
        "-GPSAltitude#",
        "-FocalLength#",
        "-FocalLengthIn35mmFormat#",
        "-ExposureTime",
        "-FNumber#",
        "-ISO#",
        "-Model",
        "-LensModel",
        "-ImageWidth",
        "-ImageHeight",
        "-j",
    ] + list(paths)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
        if data and isinstance(data, list):
            if cache_path:
                try:
                    tmp_path = f"{cache_path}.tmp.{os.getpid()}"
                    with open(tmp_path, "w", encoding="utf-8") as cf:
                        json.dump(data, cf)
                    os.replace(tmp_path, cache_path)
                except Exception:
                    pass
            return data
    except Exception as e:
        sys.stderr.write(f"ExifTool extraction failed: {e}\n")
    return []


def get_dem_patch_for_coords(lat, lon, radius_km=22.0):
    """Loads the cached USGS 3DEP DEM patch via geo_tools (radius_km=22.0 to share cache key)."""
    if lat is None or lon is None:
        return None
    try:
        gt = _import_geo_tools()
        if gt is not None and hasattr(gt, "get_dem_patch"):
            return gt.get_dem_patch(lat, lon, radius_km=radius_km)
    except Exception:
        pass
    return None


def query_usgs_elevation(lat, lon, dem=None):
    """
    Queries official USGS 3DEP ground elevation (first via local cached 3DEP DEM raster,
    falling back to EPQS HTTP API with a 2-attempt retry).
    Returns (elevation_ft, elevation_m) or (None, None).
    """
    try:
        if dem is None:
            dem = get_dem_patch_for_coords(lat, lon, radius_km=22.0)
        if dem is not None:
            e_ft, e_m = dem.sample_ft_m(lat, lon)
            if e_ft is not None:
                return e_ft, e_m
    except Exception:
        pass

    url = f"https://epqs.nationalmap.gov/v1/json?x={lon}&y={lat}&wkid=4326&units=Feet&includeDate=false"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
            with urllib.request.urlopen(req, timeout=8) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                val = res_data.get("value")
                if val is not None and val not in (-999999, -1000000) and str(val) not in ("-999999", "-1000000"):
                    elev_ft = round(float(val))
                    elev_m = round(elev_ft * 0.3048)
                    return elev_ft, elev_m
        except Exception:
            continue
    return None, None


_FIPS_STATE_MAP = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California",
    "08": "Colorado", "09": "Connecticut", "10": "Delaware", "11": "District of Columbia",
    "12": "Florida", "13": "Georgia", "15": "Hawaii", "16": "Idaho", "17": "Illinois",
    "18": "Indiana", "19": "Iowa", "20": "Kansas", "21": "Kentucky", "22": "Louisiana",
    "23": "Maine", "24": "Maryland", "25": "Massachusetts", "26": "Michigan", "27": "Minnesota",
    "28": "Mississippi", "29": "Missouri", "30": "Montana", "31": "Nebraska", "32": "Nevada",
    "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico", "36": "New York",
    "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma", "41": "Oregon",
    "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina", "46": "South Dakota",
    "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont", "51": "Virginia",
    "53": "Washington", "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming",
}


def _normalize_admin_result(raw, lat, lon):
    """Normalizes administrative boundary dict to ensure a consistent schema and formatted string."""
    if not isinstance(raw, dict):
        return None
    state = raw.get("state")
    county = raw.get("county")
    if county and "county" not in county.lower() and "parish" not in county.lower() and "borough" not in county.lower():
        county = f"{county} County"

    national_park = raw.get("national_park")
    wilderness = raw.get("wilderness")
    if wilderness:
        wilderness = re.sub(r"\s+Wilderness\s+Area$", " Wilderness", str(wilderness).strip(), flags=re.IGNORECASE)
    national_forest = raw.get("national_forest")

    fed_lands = []
    for item in (raw.get("federal_lands") or []):
        if item:
            norm_item = re.sub(r"\s+Wilderness\s+Area$", " Wilderness", str(item).strip(), flags=re.IGNORECASE)
            if norm_item and norm_item not in fed_lands:
                fed_lands.append(norm_item)
    for unit in (national_park, wilderness, national_forest):
        if unit and unit not in fed_lands:
            fed_lands.append(unit)

    parts = []
    primary_land = national_park or national_forest
    if primary_land and wilderness:
        parts.append(f"{primary_land} ({wilderness})")
    elif primary_land:
        parts.append(primary_land)
    elif wilderness:
        parts.append(wilderness)
    elif fed_lands:
        parts.append(", ".join(fed_lands))

    if county:
        parts.append(county)
    if state:
        parts.append(state)

    loc_fmt = raw.get("location_formatted") or (", ".join(parts) if parts else None)
    return {
        "state": state,
        "county": county,
        "national_park": national_park,
        "wilderness": wilderness,
        "national_forest": national_forest,
        "federal_lands": fed_lands,
        "location_formatted": loc_fmt,
    }


def query_admin_boundaries(lat, lon, timeout=3.0):
    """
    High-precision (~11 m, 4-decimal degree) cached administrative boundary lookup for County,
    State, and overlapping Federal Land units (National Park, Wilderness, National Forest).
    Enforces a strict 3.0s timeout so network slowdowns never stall metadata extraction.
    """
    if lat is None or lon is None:
        return None

    r_lat = round(float(lat), 4)
    r_lon = round(float(lon), 4)
    cache_key = f"v1_{r_lat:.4f}_{r_lon:.4f}"
    cache_path = os.path.join(tempfile.gettempdir(), f"photo_analysis_admin_bounds_{cache_key}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as cf:
                cached = json.load(cf)
            if isinstance(cached, dict) and (cached.get("county") or cached.get("state") or cached.get("federal_lands")):
                return _normalize_admin_result(cached, lat, lon)
        except Exception:
            pass

    # Delegate to geo_tools.query_admin_boundaries first if available
    gt = _import_geo_tools()
    if gt is not None and hasattr(gt, "query_admin_boundaries"):
        try:
            gt_res = gt.query_admin_boundaries(lat, lon, timeout=timeout)
            if isinstance(gt_res, dict) and (gt_res.get("county") or gt_res.get("state") or gt_res.get("federal_lands") or gt_res.get("national_park") or gt_res.get("wilderness")):
                norm_res = _normalize_admin_result(gt_res, lat, lon)
                try:
                    tmp_p = f"{cache_path}.tmp.{os.getpid()}"
                    with open(tmp_p, "w", encoding="utf-8") as cf:
                        json.dump(norm_res, cf)
                    os.replace(tmp_p, cache_path)
                except Exception:
                    pass
                return norm_res
        except Exception:
            pass

    http_timeout = max(1.0, min(float(timeout), 2.8))

    def fetch_usgs_govunits():
        # Single-request identify across County (23), National Park (29), National Monument (30),
        # National Forest (31), National Wilderness (32), USFWS (35), BLM (36)
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "sr": "4326",
            "layers": "all:23,29,30,31,32,35,36",
            "tolerance": "0",
            "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
            "imageDisplay": "100,100,96",
            "returnGeometry": "false",
            "f": "json",
        }
        url = "https://carto.nationalmap.gov/arcgis/rest/services/govunits/MapServer/identify?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("results", [])

    def fetch_census_tigerweb():
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "NAME,BASENAME,STATE",
            "returnGeometry": "false",
            "f": "json",
        }
        url = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("features", [])

    gov_results = []
    tiger_features = []
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        fut_gov = pool.submit(fetch_usgs_govunits)
        fut_tiger = pool.submit(fetch_census_tigerweb)
        try:
            gov_results = fut_gov.result(timeout=timeout)
        except Exception:
            gov_results = []
        try:
            tiger_features = fut_tiger.result(timeout=max(0.2, timeout - 0.5))
        except Exception:
            tiger_features = []
    finally:
        pool.shutdown(wait=False)

    state = None
    county = None
    national_park = None
    wilderness = None
    national_forest = None
    federal_lands = []

    for r in gov_results:
        lid = r.get("layerId")
        attrs = r.get("attributes") or {}
        name = (
            attrs.get("NAME")
            or attrs.get("name")
            or attrs.get("GNIS_NAME")
            or attrs.get("gnis_name")
            or attrs.get("COUNTY_NAME")
            or attrs.get("county_name")
        )
        if lid == 23:
            c_name = attrs.get("GNIS_NAME") or attrs.get("gnis_name") or attrs.get("COUNTY_NAME") or attrs.get("county_name")
            s_name = attrs.get("STATE_NAME") or attrs.get("state_name")
            if c_name and not county:
                county = c_name
            if s_name and not state:
                state = s_name
        elif lid in (29, 30) and name:
            if not national_park:
                national_park = name
            if name not in federal_lands:
                federal_lands.append(name)
        elif lid == 31 and name:
            if not national_forest:
                national_forest = name
            if name not in federal_lands:
                federal_lands.append(name)
        elif lid == 32 and name:
            clean_w = re.sub(r"\s+Wilderness\s+Area$", " Wilderness", str(name).strip(), flags=re.IGNORECASE)
            if not wilderness:
                wilderness = clean_w
            if clean_w not in federal_lands:
                federal_lands.append(clean_w)
        elif lid in (35, 36) and name:
            if name not in federal_lands:
                federal_lands.append(name)

    if (not county or not state) and tiger_features:
        t_attr = (tiger_features[0] or {}).get("attributes") or {}
        if not county and (t_attr.get("NAME") or t_attr.get("BASENAME")):
            county = t_attr.get("NAME") or f"{t_attr.get('BASENAME')} County"
        if not state and t_attr.get("STATE"):
            state = _FIPS_STATE_MAP.get(str(t_attr.get("STATE")).zfill(2))

    raw_out = {
        "state": state,
        "county": county,
        "national_park": national_park,
        "wilderness": wilderness,
        "national_forest": national_forest,
        "federal_lands": federal_lands,
    }
    norm_out = _normalize_admin_result(raw_out, lat, lon)
    if norm_out and (norm_out.get("county") or norm_out.get("state") or norm_out.get("federal_lands")):
        try:
            tmp_p = f"{cache_path}.tmp.{os.getpid()}"
            with open(tmp_p, "w", encoding="utf-8") as cf:
                json.dump(norm_out, cf)
            os.replace(tmp_p, cache_path)
        except Exception:
            pass
    return norm_out


def _parse_exif_dt(dt_str):
    if not dt_str or not isinstance(dt_str, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt_str.strip()[:19], fmt)
        except ValueError:
            continue
    return None


def _get_pano_base_stem(filename):
    """Returns lowercase filename stem with any '-pano...' suffix stripped for burst/stitch deduplication."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    if "-pano" in stem:
        stem = stem.split("-pano")[0]
    return stem


def _format_trail_gradient(elev_change_ft):
    if elev_change_ft is None:
        return None
    dz = int(round(elev_change_ft))
    sign_str = f"+{dz} ft" if dz > 0 else f"{dz} ft"
    if dz >= 35:
        return f"ascending (+{dz} ft)"
    elif dz <= -35:
        return f"descending ({dz} ft)"
    else:
        return f"nearly level / within GPS-DEM margin ({sign_str})"


def compute_neighbor_vectors(
    photo_path,
    all_exif,
    target_lat,
    target_lon,
    target_dt_str,
    target_elev_ft=None,
    target_elev_m=None,
    dem=None,
    return_hike_stops=False,
):
    """
    Finds chronologically preceding and succeeding photos in the same folder and computes
    walking vectors (distance and bearing), DEM-sampled elevations, noise-aware vertical
    elevation deltas, trail gradient labels, and elapsed time from prev -> current and current -> next.
    Also extracts up to 3 distinct preceding and 3 distinct succeeding hike stops (>= 25m or >= 45s apart).
    """
    target_norm = os.path.normcase(os.path.abspath(photo_path))
    items = []
    for entry in all_exif:
        src = entry.get("SourceFile")
        if not src:
            continue
        norm_p = os.path.normcase(os.path.abspath(src))
        dt_s = entry.get("DateTimeOriginal") or ""
        items.append((dt_s, os.path.basename(src), norm_p, entry))

    items.sort(key=lambda x: (x[0], x[1]))
    idx = None
    for i, item in enumerate(items):
        if item[2] == target_norm:
            idx = i
            break

    if idx is None:
        return (None, None, [], []) if return_hike_stops else (None, None)

    if dem is None and target_lat is not None and target_lon is not None:
        dem = get_dem_patch_for_coords(target_lat, target_lon, radius_km=22.0)

    if target_elev_ft is None and dem is not None and target_lat is not None and target_lon is not None:
        try:
            target_elev_ft, target_elev_m = dem.sample_ft_m(target_lat, target_lon)
        except Exception:
            pass

    target_dt_obj = _parse_exif_dt(target_dt_str)
    target_name = items[idx][1]
    target_base_stem = _get_pano_base_stem(target_name)

    def sample_neighbor_elev(n_lat, n_lon, n_exif):
        # Strictly sample from local cached DEMPatch; never fire sequential EPQS HTTP requests for neighbors
        if dem is not None and n_lat is not None and n_lon is not None:
            try:
                e_ft, e_m = dem.sample_ft_m(n_lat, n_lon)
                if e_ft is not None:
                    return e_ft, e_m
            except Exception:
                pass
        raw_alt = n_exif.get("GPSAltitude")
        if raw_alt is not None:
            try:
                e_m = round(float(raw_alt))
                e_ft = round(e_m / 0.3048)
                return e_ft, e_m
            except (ValueError, TypeError):
                pass
        return None, None

    def build_neighbor(neighbor_tuple, is_prev):
        n_dt, n_name, _, n_exif = neighbor_tuple
        n_lat = n_exif.get("GPSLatitude")
        n_lon = n_exif.get("GPSLongitude")
        n_elev_ft, n_elev_m = sample_neighbor_elev(n_lat, n_lon, n_exif)

        elev_change_ft = None
        elev_change_m = None
        if target_elev_ft is not None and n_elev_ft is not None:
            if is_prev:
                # Chronological travel from prev -> target
                elev_change_ft = int(round(target_elev_ft - n_elev_ft))
            else:
                # Chronological travel from target -> next
                elev_change_ft = int(round(n_elev_ft - target_elev_ft))
            elev_change_m = int(round(elev_change_ft * 0.3048))

        elapsed_min = None
        n_dt_obj = _parse_exif_dt(n_dt)
        if target_dt_obj is not None and n_dt_obj is not None:
            elapsed_min = round(abs((n_dt_obj - target_dt_obj).total_seconds()) / 60.0, 1)

        info = {
            "filename": n_name,
            "date_time": n_dt,
            "lat": n_lat,
            "lon": n_lon,
            "elevation_ft": n_elev_ft,
            "elevation_m": n_elev_m,
            "elevation_change_ft": elev_change_ft,
            "elevation_change_m": elev_change_m,
            "trail_gradient": _format_trail_gradient(elev_change_ft),
            "elapsed_time_min": elapsed_min,
        }
        if target_lat is not None and target_lon is not None and n_lat is not None and n_lon is not None:
            if is_prev:
                vec = calculate_haversine(n_lat, n_lon, target_lat, target_lon)
                info["travel_vector_from_prev"] = vec
            else:
                vec = calculate_haversine(target_lat, target_lon, n_lat, n_lon)
                info["travel_vector_to_next"] = vec
        return info

    def is_stationary_burst_or_same_pano(cand_tuple):
        c_dt, c_name, _, c_exif = cand_tuple
        if _get_pano_base_stem(c_name) == target_base_stem:
            return True
        c_lat = c_exif.get("GPSLatitude")
        c_lon = c_exif.get("GPSLongitude")
        if target_lat is not None and target_lon is not None and c_lat is not None and c_lon is not None:
            d_m = calculate_haversine(target_lat, target_lon, c_lat, c_lon)["distance_m"]
            if d_m < 4.0:
                return True
            return False
        c_dt_obj = _parse_exif_dt(c_dt)
        if target_dt_obj is not None and c_dt_obj is not None:
            if abs((c_dt_obj - target_dt_obj).total_seconds()) < 2.0:
                return True
        return False

    # 1. Immediate prev_photo & next_photo: skip stationary burst/pano frames (< 4.0m or same -Pano stem),
    #    falling back to closest non-same-stem neighbor if all photos in that direction are < 4.0m.
    prev_tuple = None
    prev_fallback = None
    for j in range(idx - 1, -1, -1):
        cand = items[j]
        if _get_pano_base_stem(cand[1]) != target_base_stem and prev_fallback is None:
            prev_fallback = cand
        if not is_stationary_burst_or_same_pano(cand):
            prev_tuple = cand
            break
    if prev_tuple is None:
        prev_tuple = prev_fallback or (items[idx - 1] if idx > 0 else None)

    next_tuple = None
    next_fallback = None
    for j in range(idx + 1, len(items)):
        cand = items[j]
        if _get_pano_base_stem(cand[1]) != target_base_stem and next_fallback is None:
            next_fallback = cand
        if not is_stationary_burst_or_same_pano(cand):
            next_tuple = cand
            break
    if next_tuple is None:
        next_tuple = next_fallback or (items[idx + 1] if idx + 1 < len(items) else None)

    prev_info = build_neighbor(prev_tuple, is_prev=True) if prev_tuple is not None else None
    next_info = build_neighbor(next_tuple, is_prev=False) if next_tuple is not None else None

    if not return_hike_stops:
        return prev_info, next_info

    # 2. Distinct hike stops (up to 3 preceding and 3 succeeding stops separated by >= 25m or >= 45s)
    def collect_hike_stops(indices, is_prev):
        stops = []
        anchor_lat = target_lat
        anchor_lon = target_lon
        anchor_dt = target_dt_obj
        anchor_stem = target_base_stem

        for j in indices:
            cand = items[j]
            c_dt_str, c_name, _, c_exif = cand
            c_stem = _get_pano_base_stem(c_name)
            if c_stem == anchor_stem or c_stem == target_base_stem:
                continue
            c_lat = c_exif.get("GPSLatitude")
            c_lon = c_exif.get("GPSLongitude")
            c_dt = _parse_exif_dt(c_dt_str)

            dist_from_anchor_m = None
            if anchor_lat is not None and anchor_lon is not None and c_lat is not None and c_lon is not None:
                dist_from_anchor_m = calculate_haversine(anchor_lat, anchor_lon, c_lat, c_lon)["distance_m"]

            dist_from_target_m = None
            if target_lat is not None and target_lon is not None and c_lat is not None and c_lon is not None:
                dist_from_target_m = calculate_haversine(target_lat, target_lon, c_lat, c_lon)["distance_m"]

            # Never treat a stationary frame at the exact same spot (< 4.0m) as a distinct hike stop
            if dist_from_anchor_m is not None and dist_from_anchor_m < 4.0:
                continue
            if dist_from_target_m is not None and dist_from_target_m < 4.0:
                continue

            dt_from_anchor_sec = None
            if anchor_dt is not None and c_dt is not None:
                dt_from_anchor_sec = abs((c_dt - anchor_dt).total_seconds())

            qualifies = False
            if dist_from_anchor_m is not None and dist_from_anchor_m >= 25.0:
                qualifies = True
            elif dt_from_anchor_sec is not None and dt_from_anchor_sec >= 45.0:
                qualifies = True

            if qualifies:
                stops.append(build_neighbor(cand, is_prev=is_prev))
                if c_lat is not None and c_lon is not None:
                    anchor_lat, anchor_lon = c_lat, c_lon
                if c_dt is not None:
                    anchor_dt = c_dt
                anchor_stem = c_stem
                if len(stops) >= 3:
                    break
        return stops

    prev_hike_stops = collect_hike_stops(range(idx - 1, -1, -1), is_prev=True)
    next_hike_stops = collect_hike_stops(range(idx + 1, len(items)), is_prev=False)
    return prev_info, next_info, prev_hike_stops, next_hike_stops


def _format_num_clean(val):
    if val is None:
        return None
    try:
        fval = float(val)
        if abs(fval - round(fval)) < 1e-6:
            return int(round(fval))
        return round(fval, 1)
    except (ValueError, TypeError):
        return val


def format_camera_spec(camera_model, lens_model, focal_mm, focal_35mm, hfov_deg, exposure_time, f_number, iso):
    parts = []
    if camera_model:
        parts.append(str(camera_model).strip())

    lens_part = ""
    if lens_model:
        lens_part = str(lens_model).strip()
    if focal_mm is not None:
        f_clean = _format_num_clean(focal_mm)
        if lens_part:
            lens_part = f"{lens_part} at {f_clean}mm"
        else:
            lens_part = f"{f_clean}mm"

    fov_notes = []
    if focal_35mm is not None:
        f35_clean = _format_num_clean(focal_35mm)
        fov_notes.append(f"35mm eq: {f35_clean}mm")
    if hfov_deg is not None:
        fov_notes.append(f"HFOV: {hfov_deg} deg")
    if fov_notes:
        note_str = f"({', '.join(fov_notes)})"
        lens_part = f"{lens_part} {note_str}".strip()

    if lens_part:
        parts.append(lens_part)

    if exposure_time is not None and str(exposure_time).strip() != "":
        exp_s = str(exposure_time).strip()
        if not exp_s.lower().endswith("s"):
            exp_s = f"{exp_s}s"
        parts.append(exp_s)

    if f_number is not None:
        fn_clean = _format_num_clean(f_number)
        parts.append(f"f/{fn_clean}")

    if iso is not None:
        iso_clean = _format_num_clean(iso)
        parts.append(f"ISO {iso_clean}")

    return ", ".join(parts) if parts else "[Not available]"


def main():
    parser = argparse.ArgumentParser(description="Extract photo EXIF, USGS ground elevation, lens FOV, admin boundaries, and neighbor travel vectors.")
    parser.add_argument("photo_path", nargs="?", default=None, help="Path to the target image file")
    parser.add_argument("--image", "--photo", dest="image_opt", default=None, help="Optional alias flag for the target image file path")
    parser.add_argument(
        "--ref-elev-ft",
        action="extend",
        nargs="+",
        default=[],
        help="Optional reference elevation(s) in feet (e.g. '5267' or 'Trailhead:5267') to compute relative elevation deltas",
    )
    args = parser.parse_args()

    photo_path = args.photo_path or args.image_opt
    if not photo_path:
        parser.error("A target image path is required (either positional photo_path or --image/--photo).")
    abs_photo = os.path.abspath(photo_path)
    photo_dir = os.path.dirname(abs_photo)

    # Collect sibling JPGs so we can extract neighbor coordinates in the same single ExifTool call
    batch_paths = [photo_path]
    cache_dir_key = None
    if os.path.isdir(photo_dir):
        siblings = sorted([
            os.path.join(photo_dir, f)
            for f in os.listdir(photo_dir)
            if f.lower().endswith((".jpg", ".jpeg"))
        ])
        if siblings:
            batch_paths = siblings
            try:
                dir_mtime = os.path.getmtime(photo_dir)
                cache_dir_key = f"{os.path.normcase(photo_dir)}:{dir_mtime}:{len(siblings)}"
            except OSError:
                pass

    all_exif = get_exif_batch(batch_paths, cache_dir_key=cache_dir_key)
    target_norm = os.path.normcase(abs_photo)
    exif = {}
    for entry in all_exif:
        src = entry.get("SourceFile")
        if src and os.path.normcase(os.path.abspath(src)) == target_norm:
            exif = entry
            break
    if not exif and all_exif:
        exif = all_exif[0]

    date_time = exif.get("DateTimeOriginal", "[Not available in EXIF]")
    lat = exif.get("GPSLatitude")
    lat_ref = exif.get("GPSLatitudeRef")
    lon = exif.get("GPSLongitude")
    lon_ref = exif.get("GPSLongitudeRef")
    exif_alt_m = exif.get("GPSAltitude")
    focal_length_raw = exif.get("FocalLength")
    focal_length_35mm = exif.get("FocalLengthIn35mmFormat")
    if focal_length_35mm is None and focal_length_raw is not None:
        focal_length_35mm = round(float(focal_length_raw), 1)
    elif focal_length_raw is not None:
        focal_length_raw = round(float(focal_length_raw), 1)

    exposure_time = exif.get("ExposureTime")
    if exposure_time is not None:
        exposure_time = str(exposure_time)
    f_number = _format_num_clean(exif.get("FNumber"))
    iso = _format_num_clean(exif.get("ISO"))

    img_w = exif.get("ImageWidth")
    img_h = exif.get("ImageHeight")
    hfov_deg = None
    if focal_length_35mm:
        try:
            hfov_deg = round(2.0 * math.degrees(math.atan(36.0 / (2.0 * float(focal_length_35mm)))), 1)
        except (ValueError, ZeroDivisionError):
            pass

    camera_model = exif.get("Model")
    lens_model = exif.get("LensModel")
    camera_spec_formatted = format_camera_spec(
        camera_model, lens_model, focal_length_raw, focal_length_35mm, hfov_deg, exposure_time, f_number, iso
    )

    # Stitched panorama auto-detection (-Pano filename or aspect ratio > 1.8:1)
    base_lower = os.path.basename(photo_path).lower()
    has_pano_name = "-pano" in base_lower
    wide_aspect = False
    if img_w and img_h:
        try:
            wide_aspect = (float(img_w) / float(img_h)) > 1.8
        except (ValueError, ZeroDivisionError):
            wide_aspect = False
    is_stitched_pano = bool(has_pano_name or wide_aspect)
    panorama_guidance = None
    if is_stitched_pano:
        if has_pano_name or (img_w and int(img_w) > 6720):
            panorama_guidance = (
                "Stitched multi-frame panorama detected (-Pano filename or width > 6720 px). "
                "estimated_single_frame_hfov_deg reflects a single constituent frame and does NOT represent the stitched horizontal span. "
                "Use geo_tools.py horizon-skyline --az-min <min> --az-max <max> --projection cylindrical "
                "(and crop_photo.py --all-slices 3 --grid)."
            )
        else:
            panorama_guidance = (
                "Wide aspect ratio (> 1.8:1) detected without -Pano filename suffix. "
                "Check whether this image is a multi-frame stitched panorama or a vertically letterbox-cropped single frame "
                "before overriding estimated_single_frame_hfov_deg; if stitched, use horizon-skyline --az-min <min> --az-max <max> --projection cylindrical."
            )

    if lat is not None and lon is not None:
        lat_dms = format_dms(lat, lat_ref, is_lat=True)
        lon_dms = format_dms(lon, lon_ref, is_lat=False)
        gps_dms = f"{lat_dms} {lon_dms}"
    else:
        gps_dms = "[Not available in EXIF]"

    exif_alt_ft = None
    if exif_alt_m is not None:
        try:
            exif_alt_m = round(float(exif_alt_m))
            exif_alt_ft = round(exif_alt_m / 0.3048)
        except (ValueError, TypeError):
            pass

    ground_elev_ft, ground_elev_m = None, None
    dem = None
    admin_boundaries = None
    if lat is not None and lon is not None:
        _import_geo_tools()
        dem = get_dem_patch_for_coords(lat, lon, radius_km=22.0)
        ground_elev_ft, ground_elev_m = query_usgs_elevation(lat, lon, dem=dem)
        admin_boundaries = query_admin_boundaries(lat, lon, timeout=3.0)

    chosen_ft = ground_elev_ft if ground_elev_ft is not None else exif_alt_ft
    chosen_m = ground_elev_m if ground_elev_m is not None else exif_alt_m

    elevation_str = f"{chosen_ft:,} ft / {chosen_m:,} m" if chosen_ft is not None else "[Not available]"

    elevation_deltas = []
    if chosen_ft is not None and args.ref_elev_ft:
        for ref_item in args.ref_elev_ft:
            label = "reference"
            val_str = str(ref_item)
            if ":" in val_str:
                label, val_str = val_str.split(":", 1)
            try:
                ref_ft = float(val_str.replace(",", ""))
                diff_ft = round(chosen_ft - ref_ft)
                diff_m = round(diff_ft * 0.3048)
                direction = "above" if diff_ft >= 0 else "below"
                elevation_deltas.append({
                    "label": label,
                    "ref_elevation_ft": round(ref_ft),
                    "delta_ft": abs(diff_ft),
                    "delta_m": abs(diff_m),
                    "formatted": f"~{abs(diff_ft):,} ft / {abs(diff_m):,} m {direction} {label}",
                })
            except ValueError:
                pass

    prev_photo, next_photo, prev_hike_stops, next_hike_stops = compute_neighbor_vectors(
        photo_path,
        all_exif,
        lat,
        lon,
        date_time,
        target_elev_ft=chosen_ft,
        target_elev_m=chosen_m,
        dem=dem,
        return_hike_stops=True,
    )

    result = {
        "photo_path": photo_path,
        "date_time": date_time,
        "gps_dms": gps_dms,
        "lat": lat,
        "lon": lon,
        "ground_elevation_ft": ground_elev_ft,
        "ground_elevation_m": ground_elev_m,
        "exif_elevation_ft": exif_alt_ft,
        "exif_elevation_m": exif_alt_m,
        "elevation_formatted": elevation_str,
        "admin_boundaries": admin_boundaries,
        "camera_model": camera_model,
        "lens_model": lens_model,
        "focal_length_mm": focal_length_raw,
        "focal_length_35mm": focal_length_35mm,
        "exposure_time": exposure_time,
        "f_number": f_number,
        "iso": iso,
        "camera_spec_formatted": camera_spec_formatted,
        "estimated_single_frame_hfov_deg": hfov_deg,
        "image_width": img_w,
        "image_height": img_h,
        "is_stitched_panorama_candidate": is_stitched_pano,
        "panorama_guidance": panorama_guidance,
        "prev_photo": prev_photo,
        "next_photo": next_photo,
        "prev_hike_stops": prev_hike_stops,
        "next_hike_stops": next_hike_stops,
    }
    if elevation_deltas:
        result["elevation_deltas"] = elevation_deltas

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
