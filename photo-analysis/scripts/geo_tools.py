import sys
import os
import math
import json
import hashlib
import tempfile
import urllib.request
import urllib.parse
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Prevent Windows console Unicode encoding errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    HAS_RASTER_LIBS = True
except ImportError:
    HAS_RASTER_LIBS = False


def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    dist_m = R * c
    dist_km = dist_m / 1000.0
    dist_ft = dist_m * 3.28084
    dist_mi = dist_m / 1609.344

    # Forward bearing
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    cardinals = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    cardinal_idx = int((bearing + 11.25) / 22.5) % 16
    cardinal = cardinals[cardinal_idx]

    return {
        "distance_m": round(dist_m, 1),
        "distance_km": round(dist_km, 2),
        "distance_ft": round(dist_ft, 1),
        "distance_mi": round(dist_mi, 2),
        "bearing_deg": round(bearing, 1),
        "cardinal": cardinal
    }


def get_compass_cardinal(deg):
    cardinals = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return cardinals[int((deg + 11.25) / 22.5) % 16]


def angular_diff_signed(target_deg, center_deg):
    """Returns signed angular difference (target - center) in [-180, +180]. Negative = left, Positive = right."""
    return ((target_deg - center_deg + 180.0) % 360.0) - 180.0


def project_bearing_to_fov(bearing_deg, center_az_deg, hfov_deg):
    """
    Projects a compass bearing into a rectilinear camera's horizontal field of view.
    Returns (in_fov, delta_deg, x_fraction) where x_fraction is 0.0 at left edge, 0.5 at center, 1.0 at right edge.
    """
    delta_deg = angular_diff_signed(bearing_deg, center_az_deg)
    half_fov = hfov_deg / 2.0
    if abs(delta_deg) >= 89.0 or half_fov <= 0.0:
        return False, round(delta_deg, 2), None
    tan_half = math.tan(math.radians(half_fov))
    x_frac = 0.5 + (math.tan(math.radians(delta_deg)) / (2.0 * tan_half))
    in_fov = 0.0 <= x_frac <= 1.0
    return in_fov, round(delta_deg, 2), round(x_frac, 3)


def estimate_tz_offset(lon, dt):
    # US West Coast heuristics based on longitude
    # Pacific: lon < -114.0 (PDT is -7, PST is -8)
    # Mountain: -114.0 <= lon < -104.0 (MDT is -6, MST is -7)
    is_dst = 3 <= dt.month <= 11
    if lon < -114.0:
        return -7.0 if is_dst else -8.0
    elif lon < -104.0:
        return -6.0 if is_dst else -7.0
    elif lon < -85.0:
        return -5.0 if is_dst else -6.0
    else:
        return -4.0 if is_dst else -5.0


def calculate_solar_position(lat, lon, dt, tz_offset=None):
    """
    Standard NOAA Solar Position Algorithm approximation.
    Returns solar altitude (degrees above horizon) and solar azimuth (degrees clockwise from North).
    """
    if tz_offset is None:
        tz_offset = estimate_tz_offset(lon, dt)

    # Day of year
    day_of_year = dt.timetuple().tm_yday
    # Fractional year in radians
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1.0 + (dt.hour - 12.0) / 24.0)

    # Equation of time in minutes
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2.0 * gamma) - 0.040849 * math.sin(2.0 * gamma))

    # Solar declination angle in radians
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2.0 * gamma) + 0.000907 * math.sin(2.0 * gamma)
            - 0.002697 * math.cos(3.0 * gamma) + 0.00148 * math.sin(3.0 * gamma))

    # True solar time in minutes
    time_offset = eqtime + 4.0 * lon - 60.0 * tz_offset
    tst = dt.hour * 60.0 + dt.minute + dt.second / 60.0 + time_offset
    tst = (tst + 1440.0) % 1440.0

    # Solar hour angle in degrees
    ha = (tst / 4.0) - 180.0
    ha_rad = math.radians(ha)
    lat_rad = math.radians(lat)

    # Solar zenith and altitude
    cos_zenith = (math.sin(lat_rad) * math.sin(decl) +
                  math.cos(lat_rad) * math.cos(decl) * math.cos(ha_rad))
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith_rad = math.acos(cos_zenith)
    altitude_rad = (math.pi / 2.0) - zenith_rad
    solar_altitude_deg = math.degrees(altitude_rad)

    # Solar azimuth (clockwise from North)
    cos_azimuth = ((math.sin(lat_rad) * math.cos(zenith_rad) - math.sin(decl)) /
                   (math.cos(lat_rad) * math.sin(zenith_rad)))
    cos_azimuth = max(-1.0, min(1.0, cos_azimuth))
    azimuth_rad = math.acos(cos_azimuth)
    azimuth_deg = math.degrees(azimuth_rad)

    if ha > 0.0:
        solar_azimuth = (azimuth_deg + 180.0) % 360.0
    else:
        solar_azimuth = (540.0 - azimuth_deg) % 360.0

    shadow_azimuth = (solar_azimuth + 180.0) % 360.0

    return {
        "solar_altitude_deg": round(solar_altitude_deg, 1),
        "solar_azimuth_deg": round(solar_azimuth, 1),
        "solar_cardinal": get_compass_cardinal(solar_azimuth),
        "shadow_azimuth_deg": round(shadow_azimuth, 1),
        "shadow_cardinal": get_compass_cardinal(shadow_azimuth),
        "tz_offset_hours": tz_offset
    }


class DEMPatch:
    """
    In-memory float32 USGS 3DEP Digital Elevation Model (DEM) raster patch (values in meters).
    Provides instantaneous (<0.001s) bilinear elevation lookups and ray-cast profiles.
    """
    def __init__(self, arr_m, min_lon, min_lat, max_lon, max_lat):
        self.arr_m = arr_m
        self.height, self.width = arr_m.shape
        self.min_lon = min_lon
        self.min_lat = min_lat
        self.max_lon = max_lon
        self.max_lat = max_lat

    def contains(self, lat, lon, margin_deg=0.001):
        return (
            (self.min_lat + margin_deg) <= lat <= (self.max_lat - margin_deg)
            and (self.min_lon + margin_deg) <= lon <= (self.max_lon - margin_deg)
        )

    def sample_m(self, lat, lon):
        if not (self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon):
            return None
        u = (lon - self.min_lon) / (self.max_lon - self.min_lon) * self.width - 0.5
        v = (self.max_lat - lat) / (self.max_lat - self.min_lat) * self.height - 0.5
        u = max(0.0, min(float(self.width - 1), u))
        v = max(0.0, min(float(self.height - 1), v))
        x0 = int(math.floor(u))
        y0 = int(math.floor(v))
        x1 = min(x0 + 1, self.width - 1)
        y1 = min(y0 + 1, self.height - 1)
        du = u - x0
        dv = v - y0
        v00 = float(self.arr_m[y0, x0])
        v10 = float(self.arr_m[y0, x1])
        v01 = float(self.arr_m[y1, x0])
        v11 = float(self.arr_m[y1, x1])
        if min(v00, v10, v01, v11) < -500.0 or any(math.isnan(x) for x in (v00, v10, v01, v11)):
            return None
        val = (1.0 - du) * (1.0 - dv) * v00 + du * (1.0 - dv) * v10 + (1.0 - du) * dv * v01 + du * dv * v11
        return val

    def sample_ft_m(self, lat, lon):
        m_val = self.sample_m(lat, lon)
        if m_val is None:
            return None, None
        ft_val = round(m_val * 3.280839895)
        return ft_val, round(m_val)

    def sample_peak_ft_m(self, lat, lon):
        """Samples the 3x3 pixel neighborhood maximum around a summit coordinate to account for GNIS offset."""
        if not (self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon):
            return None, None
        cx = int(round((lon - self.min_lon) / (self.max_lon - self.min_lon) * self.width - 0.5))
        cy = int(round((self.max_lat - lat) / (self.max_lat - self.min_lat) * self.height - 0.5))
        x0, x1 = max(0, cx - 1), min(self.width, cx + 2)
        y0, y1 = max(0, cy - 1), min(self.height, cy + 2)
        sub = self.arr_m[y0:y1, x0:x1]
        if sub.size == 0:
            return self.sample_ft_m(lat, lon)
        m_val = float(np.max(sub))
        if m_val < -500.0 or math.isnan(m_val):
            return None, None
        return round(m_val * 3.280839895), round(m_val)


_MEM_DEM_CACHE = {}


def get_dem_patch(lat, lon, radius_km=25.0):
    """
    Fetches or loads from disk cache a USGS 3DEP float32 GeoTIFF covering at least radius_km
    around (lat, lon). Uses a square-in-degrees bounding box with a square pixel grid so
    ArcGIS exportImage never stretches the aspect ratio.
    """
    if not HAS_RASTER_LIBS:
        return None

    # Snap center to 0.10 deg (~11 km) grid and use a square degree span so aspect ratio is exactly 1:1
    snap_lat = round(lat * 10.0) / 10.0
    snap_lon = round(lon * 10.0) / 10.0
    eff_radius_km = max(26.0, float(radius_km) + 8.0)

    d_deg = round(eff_radius_km / (111.0 * math.cos(math.radians(snap_lat))), 4)
    min_lat, max_lat = round(snap_lat - d_deg, 4), round(snap_lat + d_deg, 4)
    min_lon, max_lon = round(snap_lon - d_deg, 4), round(snap_lon + d_deg, 4)

    grid_size = 900
    cache_key = f"v2_{min_lon}_{min_lat}_{max_lon}_{max_lat}_{grid_size}"
    if cache_key in _MEM_DEM_CACHE:
        patch = _MEM_DEM_CACHE[cache_key]
        if patch.contains(lat, lon):
            return patch

    key_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()[:14]
    cache_dir = tempfile.gettempdir()
    tif_path = os.path.join(cache_dir, f"usgs_3dep_dem_v2_{key_hash}.tif")

    if not os.path.exists(tif_path):
        params = {
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "bboxSR": "4326",
            "imageSR": "4326",
            "size": f"{grid_size},{grid_size}",
            "format": "tiff",
            "pixelType": "F32",
            "noData": "-999999",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "image",
        }
        url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?" + urllib.parse.urlencode(params)
        for _ in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    raw_bytes = resp.read()
                if len(raw_bytes) > 1024:
                    tmp_path = f"{tif_path}.tmp.{os.getpid()}"
                    with open(tmp_path, "wb") as f:
                        f.write(raw_bytes)
                    os.replace(tmp_path, tif_path)
                    break
            except Exception:
                continue

    if os.path.exists(tif_path):
        try:
            with Image.open(tif_path) as img:
                arr_m = np.array(img, dtype=np.float32)
            patch = DEMPatch(arr_m, min_lon, min_lat, max_lon, max_lat)
            _MEM_DEM_CACHE[cache_key] = patch
            return patch
        except Exception:
            pass
    return None


def query_epqs_point(lat, lon):
    """Query USGS 3DEP elevation (first via local cached 3DEP DEM raster, falling back to EPQS HTTP API)."""
    dem = get_dem_patch(lat, lon, radius_km=20.0)
    if dem is not None:
        ft_val, m_val = dem.sample_ft_m(lat, lon)
        if ft_val is not None:
            return ft_val, m_val

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


def query_usgs_gnis(lat, lon, radius_km=12.0):
    """
    Queries USGS Geographic Names Information System (GNIS) ArcGIS REST service.
    Layer 5: Landforms (Summit, Ridge, Gap, Valley, Basin, Cliff, Pillar, Flat, Range)
    Layer 6: Streams (Mouth)
    Layer 7: Other Hydrographic Features (Lake, Falls, Glacier, Swamp)
    """
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    min_lat, max_lat = lat - dlat, lat + dlat
    min_lon, max_lon = lon - dlon, lon + dlon

    def fetch_layer(layer_id):
        params = {
            "geometry": f"{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "gaz_name,gaz_featureclass",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json"
        }
        url = f"https://carto.nationalmap.gov/arcgis/rest/services/geonames/MapServer/{layer_id}/query?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("features", [])
        except Exception as e:
            sys.stderr.write(f"USGS GNIS layer {layer_id} query failed: {e}\n")
            return []

    features = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for layer_items in pool.map(fetch_layer, (5, 6, 7)):
            for item in layer_items:
                attr = item.get("attributes", {})
                geom = item.get("geometry", {})
                pts = geom.get("points") or []
                if not pts:
                    continue
                f_lon, f_lat = pts[0][0], pts[0][1]
                name = attr.get("gaz_name")
                f_type = attr.get("gaz_featureclass")
                if not name or f_lat is None or f_lon is None:
                    continue
                calc = calculate_haversine(lat, lon, f_lat, f_lon)
                if calc["distance_km"] <= radius_km:
                    features.append({
                        "name": name,
                        "type": f_type,
                        "distance_mi": calc["distance_mi"],
                        "distance_km": calc["distance_km"],
                        "bearing_deg": calc["bearing_deg"],
                        "cardinal": calc["cardinal"],
                        "elevation_ft": None,
                        "elevation_m": None,
                        "lat": round(f_lat, 6),
                        "lon": round(f_lon, 6),
                        "source": "USGS GNIS"
                    })

    return features


def query_overpass_peaks(lat, lon, radius_km=10.0):
    """
    Queries Overpass API via POST with encoded payload to enrich or supplement features.
    """
    radius_m = int(min(radius_km, 22.0) * 1000)
    query = f"""[out:json][timeout:4];
(
  node(around:{radius_m},{lat},{lon})["natural"="peak"]["name"];
  node(around:{radius_m},{lat},{lon})["natural"="water"]["name"];
  node(around:{radius_m},{lat},{lon})["natural"="saddle"]["name"];
  way(around:{radius_m},{lat},{lon})["natural"="water"]["name"];
);
out center tags;
"""
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=data,
                                 headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
    features = []
    try:
        with urllib.request.urlopen(req, timeout=4.5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            for el in res_data.get("elements", []):
                tags = el.get("tags", {})
                name = tags.get("name")
                if not name:
                    continue
                f_lat = el.get("lat") or (el.get("center", {}).get("lat"))
                f_lon = el.get("lon") or (el.get("center", {}).get("lon"))
                if f_lat and f_lon:
                    calc = calculate_haversine(lat, lon, f_lat, f_lon)
                    ele_str = tags.get("ele")
                    ele_m = None
                    ele_ft = None
                    if ele_str:
                        try:
                            ele_m = round(float(str(ele_str).replace(",", "").split()[0]))
                            ele_ft = round(ele_m * 3.28084)
                        except ValueError:
                            pass
                    nat_type = tags.get("natural", "feature").capitalize()
                    if nat_type == "Peak":
                        nat_type = "Summit"
                    elif nat_type == "Water":
                        nat_type = "Lake"
                    features.append({
                        "name": name,
                        "type": nat_type,
                        "distance_mi": calc["distance_mi"],
                        "distance_km": calc["distance_km"],
                        "bearing_deg": calc["bearing_deg"],
                        "cardinal": calc["cardinal"],
                        "elevation_ft": ele_ft,
                        "elevation_m": ele_m,
                        "lat": round(f_lat, 6),
                        "lon": round(f_lon, 6),
                        "source": "OpenStreetMap"
                    })
    except Exception:
        pass

    return features


def find_nearby_features(lat, lon, radius_km=12.0):
    dem = get_dem_patch(lat, lon, radius_km=radius_km)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_gnis = pool.submit(query_usgs_gnis, lat, lon, radius_km)
        fut_osm = pool.submit(query_overpass_peaks, lat, lon, min(radius_km, 22.0))
        gnis_features = fut_gnis.result()
        osm_features = fut_osm.result()

    # Merge by normalized name, preferring entries with elevation populated
    by_name = {}
    for f in gnis_features + osm_features:
        key = f["name"].strip().lower()
        if key not in by_name:
            by_name[key] = f
        else:
            existing = by_name[key]
            if existing.get("elevation_ft") is None and f.get("elevation_ft") is not None:
                existing["elevation_ft"] = f["elevation_ft"]
                existing["elevation_m"] = f["elevation_m"]

    unique_features = list(by_name.values())
    unique_features.sort(key=lambda x: x["distance_km"])

    # Populate elevation from local 3DEP DEM patch first (instantaneous for all features!)
    if dem is not None:
        for item in unique_features:
            if item.get("elevation_ft") is None:
                if item.get("type") in ("Summit", "Ridge", "Cliff", "Pillar"):
                    e_ft, e_m = dem.sample_peak_ft_m(item["lat"], item["lon"])
                else:
                    e_ft, e_m = dem.sample_ft_m(item["lat"], item["lon"])
                if e_ft is not None:
                    item["elevation_ft"] = e_ft
                    item["elevation_m"] = e_m

    # Fallback to EPQS only for any remaining top features outside the DEM patch
    missing_elev = [
        f for f in unique_features
        if f.get("elevation_ft") is None and f.get("type") in ("Summit", "Lake", "Gap", "Saddle", "Ridge", "Basin")
    ][:8]
    if missing_elev:
        with ThreadPoolExecutor(max_workers=4) as pool:
            elevs = list(pool.map(lambda item: query_epqs_point(item["lat"], item["lon"]), missing_elev))
        for item, (e_ft, e_m) in zip(missing_elev, elevs):
            item["elevation_ft"] = e_ft
            item["elevation_m"] = e_m

    return unique_features


def compute_elevation_profile(lat1, lon1, lat2, lon2, samples=40, target_elev_ft=None, target_elev_m=None):
    """
    Computes a topographic elevation profile and line-of-sight occlusion check between
    camera (lat1, lon1) and target (lat2, lon2) using cached USGS 3DEP DEM raster (with EPQS fallback).
    Never returns a false 'VISIBLE' if endpoint or near-camera canyon-wall elevations are missing.
    """
    total_dist = calculate_haversine(lat1, lon1, lat2, lon2)
    dem = get_dem_patch(lat1, lon1, radius_km=max(20.0, total_dist["distance_km"] + 4.0))

    # Use dense sampling (default 40 steps) when DEM raster is available so narrow canyon walls are never missed
    if dem is not None and dem.contains(lat1, lon1) and dem.contains(lat2, lon2):
        samples = max(20, min(120, samples))
        coords = [
            (
                lat1 + (lat2 - lat1) * i / samples,
                lon1 + (lon2 - lon1) * i / samples,
            )
            for i in range(samples + 1)
        ]
        elevs = [dem.sample_ft_m(p[0], p[1]) for p in coords]
        if target_elev_ft is not None:
            elevs[-1] = (target_elev_ft, target_elev_m or round(target_elev_ft * 0.3048))
        else:
            pk_ft, pk_m = dem.sample_peak_ft_m(lat2, lon2)
            if pk_ft is not None and (elevs[-1][0] is None or pk_ft > elevs[-1][0]):
                elevs[-1] = (pk_ft, pk_m)
    else:
        samples = max(8, min(30, samples))
        coords = [
            (
                lat1 + (lat2 - lat1) * i / samples,
                lon1 + (lon2 - lon1) * i / samples,
            )
            for i in range(samples + 1)
        ]
        with ThreadPoolExecutor(max_workers=6) as pool:
            elevs = list(pool.map(lambda p: query_epqs_point(p[0], p[1]), coords))
        if target_elev_ft is not None:
            elevs[-1] = (target_elev_ft, target_elev_m or round(target_elev_ft * 0.3048))

    cam_ft, cam_m = elevs[0]
    tgt_ft, tgt_m = elevs[-1]
    # Add ~5 ft (1.5 m) eye/camera height above ground at camera position
    eye_ft = (cam_ft + 5) if cam_ft is not None else None

    steps = []
    max_intervening_angle = -90.0
    occluding_step = None
    missing_intermediate_count = 0
    near_camera_missing = False

    for i, ((p_lat, p_lon), (e_ft, e_m)) in enumerate(zip(coords, elevs)):
        d = calculate_haversine(lat1, lon1, p_lat, p_lon) if i > 0 else {
            "distance_m": 0.0, "distance_ft": 0.0, "distance_mi": 0.0, "distance_km": 0.0
        }
        if 0 < i < samples and e_ft is None:
            missing_intermediate_count += 1
            if i <= min(4, samples // 2):
                near_camera_missing = True

        vert_angle = None
        if i > 0 and eye_ft is not None and e_ft is not None and d["distance_m"] > 0:
            vert_angle = round(math.degrees(math.atan2((e_ft - eye_ft) * 0.3048, d["distance_m"])), 2)
            if i < samples and vert_angle > max_intervening_angle:
                max_intervening_angle = vert_angle
                occluding_step = {
                    "step": i,
                    "lat": round(p_lat, 6),
                    "lon": round(p_lon, 6),
                    "distance_mi": d["distance_mi"],
                    "distance_km": d["distance_km"],
                    "elevation_ft": e_ft,
                    "elevation_m": e_m,
                    "vertical_angle_deg": vert_angle,
                }
        steps.append({
            "step": i,
            "fraction": round(i / samples, 3),
            "lat": round(p_lat, 6),
            "lon": round(p_lon, 6),
            "distance_mi": d["distance_mi"],
            "distance_km": d["distance_km"],
            "elevation_ft": e_ft,
            "elevation_m": e_m,
            "vertical_angle_deg": vert_angle,
        })

    target_angle = steps[-1]["vertical_angle_deg"]
    is_occluded = (
        target_angle is not None
        and occluding_step is not None
        and max_intervening_angle > target_angle + 0.15
    )

    if is_occluded:
        los_status = "OCCLUDED"
    elif cam_ft is None or tgt_ft is None or target_angle is None:
        los_status = "UNKNOWN (INCOMPLETE_ELEVATION_DATA)"
    elif near_camera_missing or (samples > 2 and missing_intermediate_count / float(samples - 1) > 0.15):
        los_status = "UNKNOWN (INCOMPLETE_ELEVATION_DATA)"
    else:
        los_status = "VISIBLE"

    return {
        "bearing_deg": total_dist["bearing_deg"],
        "cardinal": total_dist["cardinal"],
        "total_distance_mi": total_dist["distance_mi"],
        "total_distance_km": total_dist["distance_km"],
        "camera_elevation_ft": cam_ft,
        "camera_elevation_m": cam_m,
        "target_elevation_ft": tgt_ft,
        "target_elevation_m": tgt_m,
        "target_vertical_angle_deg": target_angle,
        "max_intervening_angle_deg": round(max_intervening_angle, 2) if occluding_step else None,
        "line_of_sight": los_status,
        "occluding_ridge": occluding_step if is_occluded else None,
        "profile": steps,
    }


def compute_horizon_skyline(
    lat,
    lon,
    radius_km=22.0,
    az_min=0.0,
    az_max=360.0,
    az_step=1.0,
    center_az=None,
    hfov_deg=54.4,
):
    """
    Computes:
    1. True topographic DEM horizon profile across [az_min, az_max] (or around center_az +/- (hfov/2 + 20 deg)),
       extracting local horizon highpoints (including UNNAMED peaks/ridges absent from GNIS/OSM).
    2. Line-of-sight visibility and rectilinear FOV pixel-X projection for all named GNIS/OSM features.
    """
    dem = get_dem_patch(lat, lon, radius_km=radius_km)
    cam_ft, cam_m = query_epqs_point(lat, lon)
    eye_m = (cam_ft * 0.3048 + 1.5) if cam_ft is not None else None

    fov_info = None
    if center_az is not None:
        center_az = center_az % 360.0
        left_edge = (center_az - hfov_deg / 2.0) % 360.0
        right_edge = (center_az + hfov_deg / 2.0) % 360.0
        fov_info = {
            "center_azimuth_deg": round(center_az, 1),
            "center_cardinal": get_compass_cardinal(center_az),
            "hfov_deg": round(hfov_deg, 1),
            "left_edge_deg": round(left_edge, 1),
            "right_edge_deg": round(right_edge, 1),
            "note": "projected_x_fraction: 0.00 = left frame edge, 0.50 = horizontal center, 1.00 = right frame edge",
        }

    def in_azimuth_sector(bearing):
        if center_az is not None and az_min == 0.0 and az_max == 360.0:
            # Filter to FOV +/- 20 deg margin so nearby off-frame candidates are also shown clearly
            return abs(angular_diff_signed(bearing, center_az)) <= (hfov_deg / 2.0 + 20.0)
        if az_min <= az_max:
            return az_min <= bearing <= az_max
        return bearing >= az_min or bearing <= az_max

    all_named = find_nearby_features(lat, lon, radius_km=radius_km)
    candidate_named = [
        f for f in all_named
        if f.get("type") in ("Summit", "Ridge", "Gap", "Saddle", "Cliff", "Pillar", "Lake", "Basin")
        and in_azimuth_sector(f["bearing_deg"])
    ]

    named_results = []
    for f in candidate_named:
        prof = compute_elevation_profile(
            lat,
            lon,
            f["lat"],
            f["lon"],
            samples=50,
            target_elev_ft=f.get("elevation_ft"),
            target_elev_m=f.get("elevation_m"),
        )
        entry = {
            "name": f["name"],
            "type": f["type"],
            "bearing_deg": f["bearing_deg"],
            "cardinal": f["cardinal"],
            "distance_mi": f["distance_mi"],
            "distance_km": f["distance_km"],
            "elevation_ft": f["elevation_ft"] or prof["target_elevation_ft"],
            "elevation_m": f["elevation_m"] or prof["target_elevation_m"],
            "target_vertical_angle_deg": prof["target_vertical_angle_deg"],
            "max_intervening_angle_deg": prof["max_intervening_angle_deg"],
            "line_of_sight": prof["line_of_sight"],
            "lat": f["lat"],
            "lon": f["lon"],
        }
        if prof["occluding_ridge"]:
            entry["occluded_by"] = {
                "distance_mi": prof["occluding_ridge"]["distance_mi"],
                "elevation_ft": prof["occluding_ridge"]["elevation_ft"],
                "vertical_angle_deg": prof["occluding_ridge"]["vertical_angle_deg"],
                "lat": prof["occluding_ridge"]["lat"],
                "lon": prof["occluding_ridge"]["lon"],
            }
        if center_az is not None:
            in_fov, delta_deg, x_frac = project_bearing_to_fov(f["bearing_deg"], center_az, hfov_deg)
            entry["in_fov"] = in_fov
            entry["angle_from_center_deg"] = delta_deg
            entry["projected_x_fraction"] = x_frac
        named_results.append(entry)

    # Sort named results by bearing when center_az is given, else by visibility and elevation
    if center_az is not None:
        named_results.sort(key=lambda x: angular_diff_signed(x["bearing_deg"], center_az))
    else:
        named_results.sort(key=lambda x: x["bearing_deg"])

    # Compute true DEM horizon skyline rays to detect unnamed peaks and ridge crests
    horizon_peaks = []
    if dem is not None and eye_m is not None:
        ray_azimuths = []
        if center_az is not None and az_min == 0.0 and az_max == 360.0:
            half_span = min(180.0, hfov_deg / 2.0 + 15.0)
            cur = -half_span
            while cur <= half_span + 1e-6:
                ray_azimuths.append((center_az + cur) % 360.0)
                cur += az_step
        else:
            cur = az_min
            span = (az_max - az_min) if az_max >= az_min else (360.0 - az_min + az_max)
            steps_n = int(round(span / az_step))
            for i in range(steps_n + 1):
                ray_azimuths.append((az_min + i * az_step) % 360.0)

        cos_lat = math.cos(math.radians(lat))
        # Step distances from 500m out to radius_km so immediate trailside bumps don't clutter skyline highpoints
        dists_m = np.linspace(500.0, radius_km * 1000.0, 140)
        d_lats = dists_m / 111320.0
        d_lons = dists_m / (111320.0 * cos_lat)

        horizon_rays = []
        for az in ray_azimuths:
            az_rad = math.radians(az)
            sin_az, cos_az = math.sin(az_rad), math.cos(az_rad)
            best_angle = -90.0
            best_info = None
            for d_m, dlat_v, dlon_v in zip(dists_m, d_lats, d_lons):
                p_lat = lat + dlat_v * cos_az
                p_lon = lon + dlon_v * sin_az
                e_m = dem.sample_m(p_lat, p_lon)
                if e_m is None:
                    continue
                ang = math.degrees(math.atan2(e_m - eye_m, float(d_m)))
                if ang > best_angle:
                    best_angle = ang
                    best_info = (float(d_m), e_m, p_lat, p_lon)
            if best_info is not None:
                d_m, e_m, p_lat, p_lon = best_info
                e_ft = round(e_m * 3.280839895)
                horizon_rays.append({
                    "azimuth_deg": round(az, 1),
                    "cardinal": get_compass_cardinal(az),
                    "vertical_angle_deg": round(best_angle, 2),
                    "distance_mi": round(d_m / 1609.344, 2),
                    "distance_km": round(d_m / 1000.0, 2),
                    "elevation_ft": e_ft,
                    "elevation_m": round(e_m),
                    "lat": round(p_lat, 5),
                    "lon": round(p_lon, 5),
                })

        # Extract local horizon maxima (skyline peaks/highpoints across azimuths)
        for i, r in enumerate(horizon_rays):
            left_ang = horizon_rays[i - 1]["vertical_angle_deg"] if i > 0 else r["vertical_angle_deg"] - 0.1
            right_ang = horizon_rays[i + 1]["vertical_angle_deg"] if i + 1 < len(horizon_rays) else r["vertical_angle_deg"] - 0.1
            if r["vertical_angle_deg"] >= left_ang and r["vertical_angle_deg"] >= right_ang:
                # Match against known named summits within 0.45 km
                matched_name = None
                for nf in all_named:
                    if nf.get("type") in ("Summit", "Ridge", "Peak", "Cliff", "Pillar"):
                        d_match = calculate_haversine(r["lat"], r["lon"], nf["lat"], nf["lon"])
                        if d_match["distance_km"] <= 0.45:
                            matched_name = nf["name"]
                            break
                item = dict(r)
                item["matched_named_feature"] = matched_name or f"Unnamed Horizon Highpoint (~{r['elevation_ft']:,} ft / {r['elevation_m']:,} m)"
                if center_az is not None:
                    in_fov, delta_deg, x_frac = project_bearing_to_fov(r["azimuth_deg"], center_az, hfov_deg)
                    item["in_fov"] = in_fov
                    item["angle_from_center_deg"] = delta_deg
                    item["projected_x_fraction"] = x_frac
                # De-duplicate adjacent rays hitting the same DEM grid peak (within 2.5 deg)
                if horizon_peaks and abs(angular_diff_signed(item["azimuth_deg"], horizon_peaks[-1]["azimuth_deg"])) <= 2.5:
                    if item["vertical_angle_deg"] > horizon_peaks[-1]["vertical_angle_deg"]:
                        horizon_peaks[-1] = item
                else:
                    horizon_peaks.append(item)

    return {
        "camera": {
            "lat": lat,
            "lon": lon,
            "elevation_ft": cam_ft,
            "elevation_m": cam_m,
        },
        "fov": fov_info,
        "dem_horizon_highpoints": horizon_peaks,
        "named_features_visibility": named_results,
    }


def render_topo_map(lat, lon, out_path, radius_km=5.0, center_az=None, hfov_deg=54.4, rays_str=None):
    """
    Downloads a USGS Topo map centered on (lat, lon), converts to RGB, overlays camera marker,
    compass rays or FOV wedge, and nearby named summits/lakes, and writes to out_path.
    """
    if not HAS_RASTER_LIBS:
        raise RuntimeError("PIL/numpy required for render-topo")

    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    min_lat, max_lat = lat - dlat, lat + dlat
    min_lon, max_lon = lon - dlon, lon + dlon

    width = 1200
    height = int(round(width * (max_lat - min_lat) / (max_lon - min_lon)))
    params = {
        "bbox": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{width},{height}",
        "format": "png",
        "f": "image",
    }
    url = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/export?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "HikingBlogPostAnalysis/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw_png = resp.read()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp_in = f"{out_path}.raw.png"
    with open(tmp_in, "wb") as f:
        f.write(raw_png)

    img = Image.open(tmp_in).convert("RGB")
    try:
        os.remove(tmp_in)
    except OSError:
        pass

    draw = ImageDraw.Draw(img)

    def gps_to_xy(p_lat, p_lon):
        x = (p_lon - min_lon) / (max_lon - min_lon) * width
        y = (max_lat - p_lat) / (max_lat - min_lat) * height
        return x, y

    cx, cy = gps_to_xy(lat, lon)

    # Draw rays
    ray_list = []
    if rays_str:
        for part in rays_str.split(","):
            part = part.strip()
            if part:
                try:
                    ray_list.append(float(part) % 360.0)
                except ValueError:
                    pass
    elif center_az is None:
        ray_list = [float(a) for a in range(0, 360, 30)]

    ray_len_px = min(width, height) * 0.46
    for r_az in ray_list:
        rad = math.radians(r_az)
        rx = cx + ray_len_px * math.sin(rad)
        ry = cy - ray_len_px * math.cos(rad)
        draw.line([(cx, cy), (rx, ry)], fill=(90, 90, 90), width=2)
        draw.text((rx + 4, ry + 4), f"{r_az:.0f} deg ({get_compass_cardinal(r_az)})", fill=(40, 40, 40))

    # Draw FOV wedge if center_az is specified
    if center_az is not None:
        left_az = (center_az - hfov_deg / 2.0) % 360.0
        right_az = (center_az + hfov_deg / 2.0) % 360.0
        for az_val, color, label in (
            (center_az, (0, 90, 230), f"Center {center_az:.1f} deg"),
            (left_az, (0, 160, 210), f"Left FOV {left_az:.1f} deg"),
            (right_az, (0, 160, 210), f"Right FOV {right_az:.1f} deg"),
        ):
            rad = math.radians(az_val)
            rx = cx + ray_len_px * math.sin(rad)
            ry = cy - ray_len_px * math.cos(rad)
            draw.line([(cx, cy), (rx, ry)], fill=color, width=3)
            draw.text((rx + 5, ry + 5), label, fill=color)

    # Plot nearby features
    features = find_nearby_features(lat, lon, radius_km=radius_km * 1.35)
    plotted = []
    for f in features:
        fx, fy = gps_to_xy(f["lat"], f["lon"])
        if 10 <= fx <= width - 10 and 10 <= fy <= height - 10:
            color = (180, 0, 180) if f.get("type") == "Summit" else (0, 120, 180)
            draw.ellipse([fx - 5, fy - 5, fx + 5, fy + 5], fill=color, outline=(255, 255, 255))
            elev_lbl = f" ({f['elevation_ft']} ft)" if f.get("elevation_ft") else ""
            draw.text((fx + 7, fy - 7), f"{f['name']}{elev_lbl} [{f['bearing_deg']} deg]", fill=color)
            plotted.append(f)

    # Draw camera marker last so it stays on top
    draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(230, 20, 20), outline=(255, 255, 255), width=2)
    draw.text((cx + 10, cy - 10), "CAMERA", fill=(220, 0, 0))

    img.save(out_path)
    return {
        "output_path": os.path.abspath(out_path),
        "camera_lat": lat,
        "camera_lon": lon,
        "radius_km": radius_km,
        "plotted_features_count": len(plotted),
        "plotted_features": [
            {"name": p["name"], "type": p["type"], "bearing_deg": p["bearing_deg"], "distance_mi": p["distance_mi"], "elevation_ft": p["elevation_ft"]}
            for p in plotted[:20]
        ],
    }


def render_synthetic_pano(
    lat,
    lon,
    out_path,
    radius_km=22.0,
    az_min=None,
    az_max=None,
    center_az=None,
    hfov_deg=54.4,
    alt_min=-15.0,
    alt_max=15.0,
    skyline_data=None,
):
    """
    Renders a 2D front-to-back ray-marched synthetic view from the camera using the cached
    USGS 3DEP DEM raster, coloring visible terrain surfaces by distance band and flat basin/meadow
    slope, with an annotated azimuth/altitude degree grid, optional FOV bounds, and peak labels.
    """
    if not HAS_RASTER_LIBS:
        raise RuntimeError("PIL/numpy required for render-pano")

    dem = get_dem_patch(lat, lon, radius_km=radius_km)
    if dem is None:
        raise RuntimeError("Could not load USGS 3DEP DEM patch for synthetic panorama")

    if skyline_data is None:
        skyline_data = compute_horizon_skyline(
            lat,
            lon,
            radius_km=radius_km,
            az_min=0.0 if az_min is None else az_min,
            az_max=360.0 if az_max is None else az_max,
            az_step=1.0,
            center_az=center_az,
            hfov_deg=hfov_deg,
        )

    cam_m = skyline_data["camera"]["elevation_m"]
    cam_ft = skyline_data["camera"]["elevation_ft"]
    if cam_m is None:
        _, cam_m = dem.sample_ft_m(lat, lon)
        cam_ft = round(cam_m * 3.28084) if cam_m is not None else 0
    eye_m = float(cam_m) + 1.7

    # Determine azimuth sweep range
    if az_min is None or az_max is None:
        if center_az is not None:
            half_span = min(180.0, hfov_deg / 2.0 + 18.0)
            az_start = center_az - half_span
            az_span = half_span * 2.0
        else:
            az_start = 0.0
            az_span = 360.0
    else:
        az_start = float(az_min)
        az_span = (az_max - az_min) if az_max >= az_min else (360.0 - az_min + az_max)
        if az_span <= 0:
            az_span = 360.0

    n_cols = 1100 if az_span <= 140.0 else 1400
    az_offsets = np.linspace(0.0, az_span, n_cols)
    az_vals = (az_start + az_offsets) % 360.0

    # Vectorized bilinear DEM sampling across (n_cols, n_dists)
    n_dists = 550
    dists = np.linspace(50.0, radius_km * 1000.0, n_dists)
    az_rad = np.radians(az_vals)[:, None]
    cos_lat = math.cos(math.radians(lat))

    p_lats = lat + (dists[None, :] * np.cos(az_rad)) / 111139.0
    p_lons = lon + (dists[None, :] * np.sin(az_rad)) / (111139.0 * cos_lat)

    gx = (p_lons - dem.min_lon) / (dem.max_lon - dem.min_lon) * (dem.width - 1)
    gy = (dem.max_lat - p_lats) / (dem.max_lat - dem.min_lat) * (dem.height - 1)

    valid = (gx >= 0) & (gx < dem.width - 1) & (gy >= 0) & (gy < dem.height - 1)
    x0 = np.clip(gx.astype(np.int32), 0, dem.width - 2)
    y0 = np.clip(gy.astype(np.int32), 0, dem.height - 2)
    dx = gx - x0
    dy = gy - y0

    g00 = dem.arr_m[y0, x0]
    g10 = dem.arr_m[y0, x0 + 1]
    g01 = dem.arr_m[y0 + 1, x0]
    g11 = dem.arr_m[y0 + 1, x0 + 1]
    elevs = (1.0 - dx) * (1.0 - dy) * g00 + dx * (1.0 - dy) * g10 + (1.0 - dx) * dy * g01 + dx * dy * g11
    elevs = np.where(valid & np.isfinite(elevs) & (elevs > -500.0), elevs, np.nan)

    # Smoothed radial slope over ~150m window along each ray to detect flat basins/meadows/lakes cleanly
    step_m = float(dists[1] - dists[0])
    pad_e = np.pad(elevs, ((0, 0), (2, 2)), mode="edge")
    radial_grad = np.abs(pad_e[:, 4:] - pad_e[:, :-4]) / (4.0 * step_m)

    # Earth curvature & refraction correction
    r_eff = 6371000.0 / (1.0 - 0.13)
    drop = (dists[None, :] ** 2) / (2.0 * r_eff)
    apparent_h = elevs - drop
    angs = np.degrees(np.arctan2(apparent_h - eye_m, dists[None, :]))

    # Auto-adjust alt_max if steep canyon walls exceed default +15 deg
    finite_angs = angs[np.isfinite(angs)]
    if finite_angs.size > 0:
        max_terrain_ang = float(np.max(finite_angs))
        if max_terrain_ang + 3.0 > alt_max:
            alt_max = min(35.0, float(math.ceil((max_terrain_ang + 4.0) / 3.0) * 3.0))

    pano_h = 460
    pano_w = n_cols
    pano_img = np.zeros((pano_h, pano_w, 3), dtype=np.uint8)
    pano_img[:, :] = [205, 222, 255]  # sky background

    # Front-to-back vertical column painter (matching 381b83bc's synthetic_pano_annotated.png)
    for col in range(pano_w):
        max_so_far = -999.0
        prev_d = 0.0
        col_angs = angs[col]
        col_elevs = elevs[col]
        col_grad = radial_grad[col]
        for j in range(n_dists):
            ang = float(col_angs[j])
            if math.isnan(ang):
                continue
            if ang > max_so_far:
                row_start = int((alt_max - ang) / (alt_max - alt_min) * pano_h)
                row_end = int((alt_max - max_so_far) / (alt_max - alt_min) * pano_h) if max_so_far > -900.0 else pano_h
                r0 = max(0, min(pano_h, row_start))
                r1 = max(0, min(pano_h, row_end))
                if r1 > r0:
                    d = float(dists[j])
                    el = float(col_elevs[j])
                    grad = float(col_grad[j]) if not math.isnan(col_grad[j]) else 1.0
                    # Highlight flat subalpine basins/meadows/lakes below camera elevation
                    if d > 1200.0 and el < (cam_m - 60.0) and grad < 0.045:
                        color = [210, 190, 120]  # meadow/basin gold
                    elif d < 2000.0:
                        color = [60, 100, 60]    # foreground dark forest green
                    elif d < 6000.0:
                        color = [90, 120, 110]   # mid-ground green-slate ridge
                    elif d < 12000.0:
                        color = [130, 140, 170]  # distant slate-blue ridge
                    else:
                        color = [160, 170, 198]  # far horizon ridge
                    pano_img[r0:r1, col] = color
                    # Draw subtle ridge-crest contour line when distance jumps across an occluded valley
                    if prev_d > 0.0 and (d - prev_d) > 900.0 and r1 < pano_h:
                        pano_img[max(0, r1 - 1):r1, col] = [45, 55, 65]
                max_so_far = ang
                prev_d = float(dists[j])

    # Compose final canvas with axes, degree grid, FOV markers, and summit callouts
    pad_l, pad_r, pad_t, pad_b = 68, 28, 68, 52
    canvas_w = pad_l + pano_w + pad_r
    canvas_h = pad_t + pano_h + pad_b
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(Image.fromarray(pano_img), (pad_l, pad_t))
    draw = ImageDraw.Draw(canvas)

    def az_to_x(az_deg):
        diff = (az_deg - az_start) % 360.0
        if diff > az_span and (360.0 - diff) < 10.0:
            diff -= 360.0
        return pad_l + (diff / az_span) * pano_w

    def alt_to_y(alt_deg):
        return pad_t + ((alt_max - alt_deg) / (alt_max - alt_min)) * pano_h

    # Draw dashed altitude angle gridlines (every 3 degrees)
    alt_tick = math.ceil(alt_min / 3.0) * 3.0
    while alt_tick <= alt_max + 1e-6:
        y = int(round(alt_to_y(alt_tick)))
        if pad_t <= y <= pad_t + pano_h:
            line_col = (160, 70, 110) if abs(alt_tick) < 1e-4 else (190, 120, 140)
            for x_seg in range(pad_l, pad_l + pano_w, 8):
                draw.line([(x_seg, y), (min(pad_l + pano_w, x_seg + 4), y)], fill=line_col, width=1)
            draw.line([(pad_l - 5, y), (pad_l, y)], fill=(0, 0, 0), width=1)
            lbl = f"{int(round(alt_tick))} deg"
            draw.text((10, y - 6), lbl, fill=(0, 0, 0))
        alt_tick += 3.0

    # Draw dashed azimuth gridlines (every 5 deg for sector, 10 deg for wide/full)
    az_tick_step = 5.0 if az_span <= 120.0 else (10.0 if az_span <= 200.0 else 15.0)
    first_az_tick = math.ceil(az_start / az_tick_step) * az_tick_step
    cur_offset = first_az_tick - az_start
    while cur_offset <= az_span + 1e-6:
        az_t = (az_start + cur_offset) % 360.0
        x = int(round(pad_l + (cur_offset / az_span) * pano_w))
        if pad_l <= x <= pad_l + pano_w:
            for y_seg in range(pad_t, pad_t + pano_h, 8):
                draw.line([(x, y_seg), (x, min(pad_t + pano_h, y_seg + 4))], fill=(190, 120, 140), width=1)
            draw.line([(x, pad_t + pano_h), (x, pad_t + pano_h + 5)], fill=(0, 0, 0), width=1)
            card = get_compass_cardinal(az_t)
            draw.text((x - 14, pad_t + pano_h + 8), f"{int(round(az_t))}", fill=(0, 0, 0))
            draw.text((x - 12, pad_t + pano_h + 22), f"({card})", fill=(80, 80, 80))
        cur_offset += az_tick_step

    # Draw camera FOV bounds if center_az is specified
    if center_az is not None:
        left_az = (center_az - hfov_deg / 2.0) % 360.0
        right_az = (center_az + hfov_deg / 2.0) % 360.0
        for b_az, b_lbl, b_col in (
            (left_az, f"Left FOV ({left_az:.1f} deg, x=0.0)", (0, 110, 210)),
            (center_az % 360.0, f"Center ({center_az % 360.0:.1f} deg, x=0.5)", (0, 60, 200)),
            (right_az, f"Right FOV ({right_az:.1f} deg, x=1.0)", (0, 110, 210)),
        ):
            bx = int(round(az_to_x(b_az)))
            if pad_l <= bx <= pad_l + pano_w:
                for y_seg in range(pad_t, pad_t + pano_h, 10):
                    draw.line([(bx, y_seg), (bx, min(pad_t + pano_h, y_seg + 6))], fill=b_col, width=2)
                lx = max(pad_l + 4, min(pad_l + pano_w - 150, bx - 55))
                ly = pad_t + pano_h - 18
                draw.rectangle([lx - 2, ly - 1, lx + len(b_lbl) * 6 + 2, ly + 11], fill=(255, 255, 255))
                draw.text((lx, ly), b_lbl, fill=b_col)

    # Annotate VISIBLE named features and prominent unnamed DEM highpoints
    callouts = []
    for nf in skyline_data.get("named_features_visibility", []):
        if nf.get("line_of_sight") == "VISIBLE" and nf.get("target_vertical_angle_deg") is not None:
            callouts.append({
                "az": nf["bearing_deg"],
                "alt": nf["target_vertical_angle_deg"],
                "label": f"{nf['name']} ({nf['elevation_ft']}ft, {nf['bearing_deg']:.1f}deg)",
                "color": (140, 0, 140),
                "priority": 2,
            })
    for hp in skyline_data.get("dem_horizon_highpoints", []):
        if hp.get("matched_named_feature", "").startswith("Unnamed"):
            callouts.append({
                "az": hp["azimuth_deg"],
                "alt": hp["vertical_angle_deg"],
                "label": f"Peak ~{hp['elevation_ft']}ft ({hp['azimuth_deg']:.1f}deg)",
                "color": (30, 80, 140),
                "priority": 1,
            })

    # Sort callouts left-to-right and stagger vertical label rows to avoid overlap
    callouts.sort(key=lambda c: az_to_x(c["az"]))
    last_x_by_tier = [-999, -999, -999]
    for c in callouts:
        cx = int(round(az_to_x(c["az"])))
        cy = int(round(alt_to_y(c["alt"])))
        if not (pad_l + 5 <= cx <= pad_l + pano_w - 5):
            continue
        cy_clamped = max(pad_t + 4, min(pad_t + pano_h - 4, cy))
        tier = None
        for t_idx in range(3):
            if cx - last_x_by_tier[t_idx] >= 125:
                tier = t_idx
                last_x_by_tier[t_idx] = cx
                break
        if tier is None and c["priority"] >= 2:
            tier = 0
            last_x_by_tier[0] = cx
        if tier is not None:
            ly = pad_t + 6 + tier * 15
            draw.line([(cx, ly + 11), (cx, cy_clamped)], fill=c["color"], width=1)
            draw.ellipse([cx - 3, cy_clamped - 3, cx + 3, cy_clamped + 3], fill=c["color"])
            tx = max(pad_l + 2, min(pad_l + pano_w - 150, cx - 35))
            draw.rectangle([tx - 2, ly - 1, tx + len(c["label"]) * 6 + 2, ly + 11], fill=(255, 255, 255))
            draw.text((tx, ly), c["label"], fill=c["color"])

    # Border and titles
    draw.rectangle([pad_l, pad_t, pad_l + pano_w, pad_t + pano_h], outline=(0, 0, 0), width=1)
    title = f"Synthetic View from Camera ({lat:.5f}, {lon:.5f}, {cam_ft} ft / {cam_m} m)"
    if center_az is not None:
        title += f" | Center Az: {center_az:.1f} deg, HFOV: {hfov_deg:.1f} deg"
    draw.text((pad_l + 10, 12), title, fill=(0, 0, 0))
    draw.text(
        (pad_l + 10, 30),
        "Colors: Dark Green (<2km) | Slate-Green (2-6km) | Slate-Blue (6-12km) | Pale Blue (>12km) | Gold (Flat Basin/Meadow)",
        fill=(70, 70, 70),
    )
    draw.text((pad_l + pano_w // 2 - 55, pad_t + pano_h + 36), "Azimuth (degrees)", fill=(0, 0, 0))
    draw.text((6, pad_t - 18), "Alt (deg)", fill=(0, 0, 0))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    canvas.save(out_path)
    return {
        "output_path": os.path.abspath(out_path),
        "az_start_deg": round(az_start % 360.0, 1),
        "az_span_deg": round(az_span, 1),
        "alt_min_deg": round(alt_min, 1),
        "alt_max_deg": round(alt_max, 1),
        "annotated_features_count": len(callouts),
    }


def main():
    parser = argparse.ArgumentParser(description="Geographic, 3DEP DEM skyline, and solar analysis utility for hiking photos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: distance
    dist_parser = subparsers.add_parser("distance", help="Compute distance and bearing between two points.")
    dist_parser.add_argument("lat1", type=float, help="Latitude of point 1")
    dist_parser.add_argument("lon1", type=float, help="Longitude of point 1")
    dist_parser.add_argument("lat2", type=float, help="Latitude of point 2")
    dist_parser.add_argument("lon2", type=float, help="Longitude of point 2")

    # Subcommand: sun-position (and alias 'sun')
    for cmd_name in ("sun-position", "sun"):
        sun_parser = subparsers.add_parser(cmd_name, help="Compute solar elevation, solar azimuth, and shadow direction.")
        sun_parser.add_argument("lat", type=float, help="Latitude")
        sun_parser.add_argument("lon", type=float, help="Longitude")
        sun_parser.add_argument("datetime_str", help="Date and time string (YYYY-MM-DD HH:MM:SS or ISO format)")
        sun_parser.add_argument("--tz-offset", type=float, default=None, help="UTC offset hours (e.g. -7 for PDT, -8 for PST)")

    # Subcommand: nearby-features
    peaks_parser = subparsers.add_parser("nearby-features", help="Query named summits, ridges, lakes, and basins around coordinates.")
    peaks_parser.add_argument("lat", type=float, help="Latitude")
    peaks_parser.add_argument("lon", type=float, help="Longitude")
    peaks_parser.add_argument("--radius-km", type=float, default=12.0, help="Search radius in kilometers (default: 12.0)")

    # Subcommand: elevation-profile
    prof_parser = subparsers.add_parser("elevation-profile", help="Check line-of-sight occlusion and elevation profile between camera and target.")
    prof_parser.add_argument("lat1", type=float, help="Camera latitude")
    prof_parser.add_argument("lon1", type=float, help="Camera longitude")
    prof_parser.add_argument("lat2", type=float, help="Target latitude")
    prof_parser.add_argument("lon2", type=float, help="Target longitude")
    prof_parser.add_argument("--samples", type=int, default=40, help="Number of sample intervals along ray (default: 40)")

    # Subcommand: horizon-skyline
    sky_parser = subparsers.add_parser(
        "horizon-skyline",
        help="Compute 3DEP DEM topographic horizon (including unnamed peaks), named summit visibility, and rectilinear FOV pixel-X projections.",
    )
    sky_parser.add_argument("lat", type=float, help="Camera latitude")
    sky_parser.add_argument("lon", type=float, help="Camera longitude")
    sky_parser.add_argument("--radius-km", type=float, default=22.0, help="Search radius in kilometers (default: 22.0)")
    sky_parser.add_argument("--az-min", type=float, default=0.0, help="Minimum azimuth in degrees (default: 0.0)")
    sky_parser.add_argument("--az-max", type=float, default=360.0, help="Maximum azimuth in degrees (default: 360.0)")
    sky_parser.add_argument("--az-step", type=float, default=1.0, help="Azimuth step in degrees for DEM ray sweep (default: 1.0)")
    sky_parser.add_argument("--center-az", type=float, default=None, help="Estimated camera center azimuth (degrees) to compute FOV bounds and pixel-X fraction")
    sky_parser.add_argument("--hfov-deg", type=float, default=54.4, help="Horizontal field of view in degrees (default: 54.4 for 35mm full-frame)")
    sky_parser.add_argument("--out-pano", default=None, help="Optional PNG path to also render an annotated 2D synthetic DEM panorama (e.g. scratch/281A5933_pano.png)")

    # Subcommand: render-pano
    pano_parser = subparsers.add_parser(
        "render-pano",
        help="Render a 2D front-to-back ray-marched synthetic DEM panorama with azimuth/altitude degree grid, FOV bounds, and peak labels.",
    )
    pano_parser.add_argument("lat", type=float, help="Camera latitude")
    pano_parser.add_argument("lon", type=float, help="Camera longitude")
    pano_parser.add_argument("--out", required=True, help="Output PNG file path (e.g. scratch/281A5933_pano.png)")
    pano_parser.add_argument("--radius-km", type=float, default=22.0, help="Ray-marching radius in kilometers (default: 22.0)")
    pano_parser.add_argument("--az-min", type=float, default=None, help="Minimum azimuth in degrees (default: auto from --center-az or 0)")
    pano_parser.add_argument("--az-max", type=float, default=None, help="Maximum azimuth in degrees (default: auto from --center-az or 360)")
    pano_parser.add_argument("--center-az", type=float, default=None, help="Optional camera center azimuth in degrees to draw FOV bounds")
    pano_parser.add_argument("--hfov-deg", type=float, default=54.4, help="Horizontal field of view in degrees (default: 54.4)")
    pano_parser.add_argument("--alt-min", type=float, default=-15.0, help="Minimum altitude angle in degrees (default: -15.0)")
    pano_parser.add_argument("--alt-max", type=float, default=15.0, help="Maximum altitude angle in degrees (default: 15.0, auto-expands for steep walls)")

    # Subcommand: render-topo
    topo_parser = subparsers.add_parser(
        "render-topo",
        help="Download and annotate a USGS Topo map centered on camera with optional FOV wedge, bearing rays, and nearby summits.",
    )
    topo_parser.add_argument("lat", type=float, help="Camera latitude")
    topo_parser.add_argument("lon", type=float, help="Camera longitude")
    topo_parser.add_argument("--out", required=True, help="Output PNG file path (e.g. scratch/281A5985_topo.png)")
    topo_parser.add_argument("--radius-km", type=float, default=5.0, help="Map half-width radius in kilometers (default: 5.0)")
    topo_parser.add_argument("--center-az", type=float, default=None, help="Optional camera center azimuth to draw FOV wedge")
    topo_parser.add_argument("--hfov-deg", type=float, default=54.4, help="Horizontal FOV in degrees when drawing FOV wedge (default: 54.4)")
    topo_parser.add_argument("--rays", default=None, help="Optional comma-separated bearing rays in degrees (e.g. '90,180,270')")

    args = parser.parse_args()

    if args.command == "distance":
        res = calculate_haversine(args.lat1, args.lon1, args.lat2, args.lon2)
        print(json.dumps(res, indent=2))

    elif args.command in ("sun-position", "sun"):
        raw_dt = args.datetime_str.strip()
        tz_override = args.tz_offset
        # Strip trailing timezone offset like -07:00 if present in ISO string
        if len(raw_dt) > 19 and (raw_dt[19] in ("+", "-")):
            try:
                sign = -1.0 if raw_dt[19] == "-" else 1.0
                parts = raw_dt[20:].split(":")
                if tz_override is None:
                    tz_override = sign * (float(parts[0]) + (float(parts[1]) / 60.0 if len(parts) > 1 else 0.0))
                raw_dt = raw_dt[:19]
            except ValueError:
                pass

        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(raw_dt, fmt)
                break
            except ValueError:
                dt = None
        if not dt:
            sys.stderr.write(f"Could not parse datetime string: {args.datetime_str}\n")
            sys.exit(1)

        res = calculate_solar_position(args.lat, args.lon, dt, tz_override)
        res["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        res["lat"] = args.lat
        res["lon"] = args.lon
        print(json.dumps(res, indent=2))

    elif args.command == "nearby-features":
        features = find_nearby_features(args.lat, args.lon, args.radius_km)
        print(json.dumps(features, indent=2))

    elif args.command == "elevation-profile":
        res = compute_elevation_profile(args.lat1, args.lon1, args.lat2, args.lon2, args.samples)
        print(json.dumps(res, indent=2))

    elif args.command == "horizon-skyline":
        res = compute_horizon_skyline(
            args.lat,
            args.lon,
            radius_km=args.radius_km,
            az_min=args.az_min,
            az_max=args.az_max,
            az_step=args.az_step,
            center_az=args.center_az,
            hfov_deg=args.hfov_deg,
        )
        if args.out_pano:
            pano_info = render_synthetic_pano(
                args.lat,
                args.lon,
                out_path=args.out_pano,
                radius_km=args.radius_km,
                az_min=None if (args.az_min == 0.0 and args.az_max == 360.0 and args.center_az is not None) else args.az_min,
                az_max=None if (args.az_min == 0.0 and args.az_max == 360.0 and args.center_az is not None) else args.az_max,
                center_az=args.center_az,
                hfov_deg=args.hfov_deg,
                skyline_data=res,
            )
            res["synthetic_pano"] = pano_info
        print(json.dumps(res, indent=2))

    elif args.command == "render-pano":
        res = render_synthetic_pano(
            args.lat,
            args.lon,
            out_path=args.out,
            radius_km=args.radius_km,
            az_min=args.az_min,
            az_max=args.az_max,
            center_az=args.center_az,
            hfov_deg=args.hfov_deg,
            alt_min=args.alt_min,
            alt_max=args.alt_max,
        )
        print(json.dumps(res, indent=2))

    elif args.command == "render-topo":
        res = render_topo_map(
            args.lat,
            args.lon,
            out_path=args.out,
            radius_km=args.radius_km,
            center_az=args.center_az,
            hfov_deg=args.hfov_deg,
            rays_str=args.rays,
        )
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
