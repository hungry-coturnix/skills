import sys
import os
import math
import json
import hashlib
import tempfile
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

# Prevent Windows console Unicode encoding errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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
        cache_path = os.path.join(tempfile.gettempdir(), f"photo_analysis_exif_batch_{dir_hash}.json")
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


def query_usgs_elevation(lat, lon):
    """
    Queries official USGS 3DEP ground elevation (first via local cached 3DEP DEM raster,
    falling back to EPQS HTTP API with a 2-attempt retry).
    Returns (elevation_ft, elevation_m) or (None, None).
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        import geo_tools
        dem = geo_tools.get_dem_patch(lat, lon, radius_km=16.0)
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
            with urllib.request.urlopen(req, timeout=10) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                val = res_data.get("value")
                if val is not None and val not in (-999999, -1000000) and str(val) not in ("-999999", "-1000000"):
                    elev_ft = round(float(val))
                    elev_m = round(elev_ft * 0.3048)
                    return elev_ft, elev_m
        except Exception:
            continue
    return None, None


def compute_neighbor_vectors(photo_path, all_exif, target_lat, target_lon, target_dt_str):
    """
    Finds chronologically preceding and succeeding photos in the same folder and computes
    walking vectors (distance and bearing) from prev -> current and current -> next.
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
        return None, None

    def build_neighbor(neighbor_tuple, is_prev):
        n_dt, n_name, _, n_exif = neighbor_tuple
        n_lat = n_exif.get("GPSLatitude")
        n_lon = n_exif.get("GPSLongitude")
        info = {
            "filename": n_name,
            "date_time": n_dt,
            "lat": n_lat,
            "lon": n_lon,
        }
        if target_lat is not None and target_lon is not None and n_lat is not None and n_lon is not None:
            if is_prev:
                vec = calculate_haversine(n_lat, n_lon, target_lat, target_lon)
                info["travel_vector_from_prev"] = vec
            else:
                vec = calculate_haversine(target_lat, target_lon, n_lat, n_lon)
                info["travel_vector_to_next"] = vec
        return info

    prev_info = build_neighbor(items[idx - 1], is_prev=True) if idx > 0 else None
    next_info = build_neighbor(items[idx + 1], is_prev=False) if idx + 1 < len(items) else None
    return prev_info, next_info


def main():
    parser = argparse.ArgumentParser(description="Extract photo EXIF, USGS ground elevation, lens FOV, and neighbor travel vectors.")
    parser.add_argument("photo_path", help="Path to the target image file")
    parser.add_argument(
        "--ref-elev-ft",
        action="extend",
        nargs="+",
        default=[],
        help="Optional reference elevation(s) in feet (e.g. '5267' or 'Trailhead:5267') to compute relative elevation deltas",
    )
    args = parser.parse_args()

    photo_path = args.photo_path
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

    img_w = exif.get("ImageWidth")
    img_h = exif.get("ImageHeight")
    hfov_deg = None
    if focal_length_35mm:
        try:
            hfov_deg = round(2.0 * math.degrees(math.atan(36.0 / (2.0 * float(focal_length_35mm)))), 1)
        except (ValueError, ZeroDivisionError):
            pass

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
    if lat is not None and lon is not None:
        ground_elev_ft, ground_elev_m = query_usgs_elevation(lat, lon)

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

    prev_photo, next_photo = compute_neighbor_vectors(photo_path, all_exif, lat, lon, date_time)

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
        "camera_model": exif.get("Model"),
        "lens_model": exif.get("LensModel"),
        "focal_length_mm": focal_length_raw,
        "focal_length_35mm": focal_length_35mm,
        "estimated_single_frame_hfov_deg": hfov_deg,
        "image_width": img_w,
        "image_height": img_h,
        "prev_photo": prev_photo,
        "next_photo": next_photo,
    }
    if elevation_deltas:
        result["elevation_deltas"] = elevation_deltas

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
