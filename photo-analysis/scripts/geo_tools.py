import sys
import os
import math
import json
import time
import hashlib
import tempfile
import threading
import urllib.request
import urllib.parse
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Prevent Windows console Unicode encoding errors and enforce line buffering for background task logs
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    HAS_RASTER_LIBS = True
except ImportError:
    HAS_RASTER_LIBS = False


def _fetch_url_bytes(url, data=None, timeout=10.0, headers=None):
    """
    Fetches raw bytes from a URL with both a socket timeout and a hard daemon-thread join guard
    so a stalled SSL/socket read on Windows can never block indefinitely.
    """
    req_headers = {"User-Agent": "HikingBlogPostAnalysis/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    result = [None, None]

    def _worker():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result[0] = resp.read()
        except Exception as exc:
            result[1] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout + 2.0)
    if t.is_alive():
        raise TimeoutError(f"Hard timeout ({timeout}s) exceeded reading {url}")
    if result[1] is not None:
        raise result[1]
    return result[0]



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


def project_bearing_to_fov(bearing_deg, center_az_deg, hfov_deg, projection="rectilinear"):
    """
    Projects a compass bearing into a camera's horizontal field of view (rectilinear or cylindrical).
    Returns (in_fov, delta_deg, x_fraction) where x_fraction is 0.0 at left edge, 0.5 at center, 1.0 at right edge.
    """
    delta_deg = angular_diff_signed(bearing_deg, center_az_deg)
    if hfov_deg <= 0.0:
        return False, round(delta_deg, 2), None

    proj_mode = (projection or "rectilinear").lower()
    if proj_mode == "auto":
        proj_mode = "cylindrical" if hfov_deg > 100.0 else "rectilinear"

    if proj_mode == "cylindrical":
        x_frac = 0.5 + (delta_deg / hfov_deg)
        in_fov = 0.0 <= x_frac <= 1.0
        return in_fov, round(delta_deg, 2), round(x_frac, 3)

    half_fov = hfov_deg / 2.0
    if abs(delta_deg) >= 89.0 or half_fov <= 0.0 or half_fov >= 89.0:
        return False, round(delta_deg, 2), None
    tan_half = math.tan(math.radians(half_fov))
    x_frac = 0.5 + (math.tan(math.radians(delta_deg)) / (2.0 * tan_half))
    in_fov = 0.0 <= x_frac <= 1.0
    return in_fov, round(delta_deg, 2), round(x_frac, 3)


def project_coordinate(lat1, lon1, bearing_deg, dist_km):
    """
    Spherical forward projection from (lat1, lon1) along bearing_deg (degrees clockwise from North)
    for dist_km (kilometers). Returns (lat2, lon2).
    """
    R_km = 6371.0
    ang_dist = float(dist_km) / R_km
    phi1 = math.radians(float(lat1))
    lam1 = math.radians(float(lon1))
    theta = math.radians(float(bearing_deg) % 360.0)

    sin_phi2 = math.sin(phi1) * math.cos(ang_dist) + math.cos(phi1) * math.sin(ang_dist) * math.cos(theta)
    sin_phi2 = max(-1.0, min(1.0, sin_phi2))
    phi2 = math.asin(sin_phi2)
    y = math.sin(theta) * math.sin(ang_dist) * math.cos(phi1)
    x = math.cos(ang_dist) - math.sin(phi1) * math.sin(phi2)
    lam2 = lam1 + math.atan2(y, x)
    lat2 = math.degrees(phi2)
    lon2 = ((math.degrees(lam2) + 540.0) % 360.0) - 180.0
    return round(lat2, 6), round(lon2, 6)


def calibrate_azimuth(
    hfov_deg,
    anchors=None,
    projection="rectilinear",
    center_az=None,
    x_fracs=None,
    project_bearings=None,
):
    """
    Inverts the horizontal FOV projection formula for one or more anchor landmarks
    (each given as 'bearing_deg:x_fraction', 'Name:bearing_deg:x_fraction', or (bearing_deg, x_fraction))
    to solve for camera center_azimuth_deg, left_edge_deg, and right_edge_deg, OR accepts an explicit
    center_az and performs forward x_frac -> bearing lookups and/or multi-bearing -> x_frac projections.
    """
    proj_mode = (projection or "rectilinear").lower()
    if proj_mode == "auto":
        proj_mode = "cylindrical" if hfov_deg > 100.0 else "rectilinear"

    parsed_anchors = []
    if anchors:
        for raw in anchors:
            label = None
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                b_deg, x_frac = float(raw[0]), float(raw[1])
            elif isinstance(raw, dict):
                b_deg = float(raw["bearing_deg"])
                x_frac = float(raw["x_fraction"])
                label = raw.get("name")
            else:
                raw_s = str(raw)
                sep = ":" if ":" in raw_s else ","
                parts = [p.strip() for p in raw_s.split(sep)]
                if len(parts) == 2:
                    b_deg, x_frac = float(parts[0]), float(parts[1])
                elif len(parts) >= 3:
                    label = ":".join(parts[:-2])
                    b_deg, x_frac = float(parts[-2]), float(parts[-1])
                else:
                    raise ValueError(f"Invalid anchor specification '{raw}'. Expected 'bearing_deg:x_fraction'.")

            if proj_mode == "cylindrical" or hfov_deg >= 175.0:
                delta_deg = (x_frac - 0.5) * hfov_deg
            else:
                tan_half = math.tan(math.radians(hfov_deg / 2.0))
                delta_deg = math.degrees(math.atan((x_frac - 0.5) * 2.0 * tan_half))

            implied_center = (b_deg - delta_deg) % 360.0
            entry = {
                "landmark_bearing_deg": round(b_deg, 2),
                "observed_x_fraction": round(x_frac, 3),
                "offset_from_center_deg": round(delta_deg, 2),
                "implied_center_azimuth_deg": round(implied_center, 2),
            }
            if label:
                entry["name"] = label
            parsed_anchors.append(entry)

    if parsed_anchors:
        # Circular mean of implied center azimuths
        sin_sum = sum(math.sin(math.radians(a["implied_center_azimuth_deg"])) for a in parsed_anchors)
        cos_sum = sum(math.cos(math.radians(a["implied_center_azimuth_deg"])) for a in parsed_anchors)
        solved_center_az = (math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0
        for a in parsed_anchors:
            a["residual_deg"] = round(angular_diff_signed(a["implied_center_azimuth_deg"], solved_center_az), 2)
    elif center_az is not None:
        solved_center_az = float(center_az) % 360.0
    else:
        raise ValueError("At least one of --anchor or --center-az is required for calibrate-azimuth")

    left_edge = (solved_center_az - hfov_deg / 2.0) % 360.0
    right_edge = (solved_center_az + hfov_deg / 2.0) % 360.0

    out = {
        "projection": proj_mode,
        "hfov_deg": round(hfov_deg, 1),
        "center_azimuth_deg": round(solved_center_az, 1),
        "center_cardinal": get_compass_cardinal(solved_center_az),
        "left_edge_deg": round(left_edge, 1),
        "right_edge_deg": round(right_edge, 1),
        "anchors": parsed_anchors,
    }

    if x_fracs:
        raw_x_tokens = []
        if isinstance(x_fracs, (list, tuple)):
            for item in x_fracs:
                raw_x_tokens.extend(p.strip() for p in str(item).split(",") if p.strip())
        else:
            raw_x_tokens.extend(p.strip() for p in str(x_fracs).split(",") if p.strip())

        x_proj_list = []
        for tok in raw_x_tokens:
            x_label = None
            if ":" in tok:
                x_label_part, x_val_part = tok.rsplit(":", 1)
                x_label = x_label_part.strip()
                x_f = float(x_val_part.strip())
            else:
                x_f = float(tok)

            if proj_mode == "cylindrical" or hfov_deg >= 175.0:
                delta_deg = (x_f - 0.5) * hfov_deg
            else:
                tan_half = math.tan(math.radians(hfov_deg / 2.0))
                delta_deg = math.degrees(math.atan((x_f - 0.5) * 2.0 * tan_half))

            b_deg = (solved_center_az + delta_deg) % 360.0
            x_entry = {}
            if x_label:
                x_entry["name"] = x_label
            x_entry.update({
                "x_fraction": round(x_f, 3),
                "bearing_deg": round(b_deg, 2),
                "cardinal": get_compass_cardinal(b_deg),
                "offset_from_center_deg": round(delta_deg, 2),
            })
            x_proj_list.append(x_entry)
        out["x_fraction_projections"] = x_proj_list

    if project_bearings:
        raw_b_tokens = []
        if isinstance(project_bearings, (list, tuple)):
            for item in project_bearings:
                raw_b_tokens.extend(p.strip() for p in str(item).split(",") if p.strip())
        else:
            raw_b_tokens.extend(p.strip() for p in str(project_bearings).split(",") if p.strip())

        b_proj_list = []
        for tok in raw_b_tokens:
            b_label = None
            if ":" in tok:
                b_label_part, b_val_part = tok.rsplit(":", 1)
                b_label = b_label_part.strip()
                b_val = float(b_val_part.strip()) % 360.0
            else:
                b_val = float(tok) % 360.0

            in_fov, delta_deg, x_frac = project_bearing_to_fov(
                b_val, solved_center_az, hfov_deg, projection=proj_mode
            )
            b_entry = {}
            if b_label:
                b_entry["name"] = b_label
            b_entry.update({
                "bearing_deg": round(b_val, 2),
                "cardinal": get_compass_cardinal(b_val),
                "in_fov": in_fov,
                "projected_x_fraction": x_frac,
                "angle_from_center_deg": delta_deg,
            })
            b_proj_list.append(b_entry)
        out["bearing_projections"] = b_proj_list

    return out


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


def _shadow_2d_direction_for_camera(shadow_azimuth, cam_az):
    """
    Translates a 3D ground shadow azimuth into a flat-ground 2D image-plane shadow direction
    for a camera facing cam_az.
    """
    rel_deg = angular_diff_signed(shadow_azimuth, cam_az)
    if -22.5 <= rel_deg <= 22.5:
        desc = "AWAY_INTO_SCENE (12 o'clock / up)"
    elif 22.5 < rel_deg <= 67.5:
        desc = "DIAGONAL_AWAY_RIGHT (1-2 o'clock / up-right, away)"
    elif 67.5 < rel_deg <= 112.5:
        desc = "LEFT_TO_RIGHT (3 o'clock / right)"
    elif 112.5 < rel_deg <= 157.5:
        desc = "DIAGONAL_TOWARD_RIGHT (4-5 o'clock / down-right, toward camera)"
    elif rel_deg > 157.5 or rel_deg < -157.5:
        desc = "TOWARD_CAMERA (6 o'clock / down)"
    elif -157.5 <= rel_deg < -112.5:
        desc = "DIAGONAL_TOWARD_LEFT (7-8 o'clock / down-left, toward camera)"
    elif -112.5 <= rel_deg < -67.5:
        desc = "RIGHT_TO_LEFT (9 o'clock / left)"
    else:
        desc = "DIAGONAL_AWAY_LEFT (10-11 o'clock / up-left, away)"
    return round(rel_deg, 1), desc


def _calculate_lunar_ephemeris(lat, lon, dt, tz_offset):
    """
    Pure-Python standard-library (math-only) Meeus lunar ephemeris (Ch. 47, 48, 40)
    with topocentric horizontal parallax correction.
    Returns dict of lunar_* fields for sun-position output.
    """
    utc_days = (dt - datetime(2000, 1, 1, 12, 0, 0)).total_seconds() / 86400.0 - (float(tz_offset) / 24.0)
    T = utc_days / 36525.0
    # Terrestrial Time (TT = UTC + ~69.184 s) for lunar orbital arguments
    T_tt = (utc_days + 69.184 / 86400.0) / 36525.0

    # Fundamental arguments (Meeus Ch. 47, degrees)
    Lp = (
        218.3164477
        + 481267.88123421 * T_tt
        - 0.0015786 * (T_tt ** 2)
        + (T_tt ** 3) / 538841.0
        - (T_tt ** 4) / 65194000.0
    ) % 360.0
    D = (
        297.8501921
        + 445267.1114034 * T_tt
        - 0.0018819 * (T_tt ** 2)
        + (T_tt ** 3) / 545868.0
        - (T_tt ** 4) / 113065000.0
    ) % 360.0
    M = (
        357.5291092
        + 35999.0502909 * T_tt
        - 0.0001536 * (T_tt ** 2)
        + (T_tt ** 3) / 24490000.0
    ) % 360.0
    Mp = (
        134.9633964
        + 477198.8675055 * T_tt
        + 0.0087414 * (T_tt ** 2)
        + (T_tt ** 3) / 69699.0
        - (T_tt ** 4) / 14712000.0
    ) % 360.0
    F = (
        93.2720950
        + 483202.0175233 * T_tt
        - 0.0036539 * (T_tt ** 2)
        - (T_tt ** 3) / 3526000.0
        + (T_tt ** 4) / 863310000.0
    ) % 360.0

    A1 = (119.75 + 131.849 * T_tt) % 360.0
    A2 = (53.09 + 479264.290 * T_tt) % 360.0
    A3 = (313.45 + 481266.484 * T_tt) % 360.0
    E = 1.0 - 0.002516 * T_tt - 0.0000074 * (T_tt ** 2)

    # Meeus Table 47.A principal periodic terms: (D, M, Mp, F, coeff_l, coeff_r)
    terms_lr = (
        (0, 0, 1, 0, 6288774, -20905355),
        (2, 0, -1, 0, 1274027, -3699111),
        (2, 0, 0, 0, 658314, -2955968),
        (0, 0, 2, 0, 213618, -569925),
        (0, 1, 0, 0, -185116, 48888),
        (0, 0, 0, 2, -114332, -3149),
        (2, 0, -2, 0, 58793, 246158),
        (2, -1, -1, 0, 57066, -152138),
        (2, 0, 1, 0, 53322, -170733),
        (2, -1, 0, 0, 45758, -204586),
        (0, 1, -1, 0, -40923, -129620),
        (1, 0, 0, 0, -34720, 108743),
        (0, 1, 1, 0, -30383, 104755),
        (2, 0, 0, -2, 15327, 10321),
        (0, 0, 1, 2, -12528, 0),
        (0, 0, 1, -2, 10980, 79661),
        (4, 0, -1, 0, 10675, -34782),
        (0, 0, 3, 0, 10034, -23210),
        (4, 0, -2, 0, 8548, -21636),
        (2, 1, -1, 0, -7888, 24208),
        (2, 1, 0, 0, -6766, 30824),
        (1, 0, -1, 0, -5163, -8379),
        (1, 1, 0, 0, 4987, -16675),
        (2, -1, 1, 0, 4036, -12831),
        (2, 0, 2, 0, 3994, -10445),
        (4, 0, 0, 0, 3861, -11650),
        (2, 0, -3, 0, 3665, 14403),
        (0, 1, -2, 0, -2689, -7003),
        (2, 0, -1, 2, -2602, 0),
        (2, -1, -2, 0, 2390, 10056),
        (1, 0, 1, 0, -2348, 6322),
        (2, -2, 0, 0, 2236, -9884),
        (0, 1, 2, 0, -2120, 5751),
        (0, 2, 0, 0, -2069, 0),
        (2, -2, -1, 0, 2048, -4950),
        (2, 0, 1, -2, -1773, 4130),
        (2, 0, 0, 2, -1595, 0),
        (4, -1, -1, 0, 1215, -3958),
        (0, 0, 2, 2, -1110, 0),
        (3, 0, -1, 0, -892, 3258),
        (2, 1, 1, 0, -810, 2616),
        (4, -1, -2, 0, 759, -1897),
        (0, 2, -1, 0, -713, -2117),
        (2, 2, -1, 0, -700, 2354),
        (2, 1, -2, 0, 691, 0),
        (2, -1, 0, -2, 596, 0),
        (4, 0, 1, 0, 549, -1423),
        (0, 0, 4, 0, 537, -1117),
        (4, -1, 0, 0, 520, -1571),
        (1, 0, -2, 0, -487, -1739),
    )

    # Meeus Table 47.B principal periodic terms for latitude: (D, M, Mp, F, coeff_b)
    terms_b = (
        (0, 0, 0, 1, 5128122),
        (0, 0, 1, 1, 280602),
        (0, 0, 1, -1, 277693),
        (2, 0, 0, -1, 173237),
        (2, 0, -1, 1, 55413),
        (2, 0, -1, -1, 46271),
        (2, 0, 0, 1, 32573),
        (0, 0, 2, 1, 17198),
        (2, 0, 1, -1, 9266),
        (0, 0, 2, -1, 8822),
        (2, -1, 0, -1, 8216),
        (2, 0, -2, -1, 4324),
        (2, 0, 1, 1, 4200),
        (2, 1, 0, -1, -3359),
        (2, -1, -1, 1, 2463),
        (2, -1, 0, 1, 2211),
        (2, -1, -1, -1, 2065),
        (0, 1, -1, -1, -1870),
        (4, 0, -1, -1, 1828),
        (0, 1, 0, 1, -1794),
        (0, 0, 0, 3, -1749),
        (0, 1, -1, 1, -1565),
        (1, 0, 0, 1, -1491),
        (0, 1, 1, 1, -1475),
        (0, 1, 1, -1, -1410),
        (0, 1, 0, -1, -1344),
        (1, 0, 0, -1, -1335),
        (0, 0, 3, 1, 1107),
        (4, 0, 0, -1, 1021),
        (4, 0, -1, 1, 833),
    )

    sum_l = 0.0
    sum_r = 0.0
    for d_c, m_c, mp_c, f_c, cl, cr in terms_lr:
        arg_rad = math.radians(d_c * D + m_c * M + mp_c * Mp + f_c * F)
        ef = (E ** abs(m_c)) if m_c != 0 else 1.0
        sum_l += cl * ef * math.sin(arg_rad)
        if cr != 0:
            sum_r += cr * ef * math.cos(arg_rad)

    sum_l += (
        3958.0 * math.sin(math.radians(A1))
        + 1962.0 * math.sin(math.radians(Lp - F))
        + 318.0 * math.sin(math.radians(A2))
    )

    sum_b = 0.0
    for d_c, m_c, mp_c, f_c, cb in terms_b:
        arg_rad = math.radians(d_c * D + m_c * M + mp_c * Mp + f_c * F)
        ef = (E ** abs(m_c)) if m_c != 0 else 1.0
        sum_b += cb * ef * math.sin(arg_rad)

    sum_b += (
        -2235.0 * math.sin(math.radians(Lp))
        + 382.0 * math.sin(math.radians(A3))
        + 175.0 * math.sin(math.radians(A1 - F))
        + 175.0 * math.sin(math.radians(A1 + F))
        + 127.0 * math.sin(math.radians(Lp - Mp))
        - 115.0 * math.sin(math.radians(Lp + Mp))
    )

    # Geocentric ecliptic longitude, latitude, and distance (km)
    lam_deg = (Lp + sum_l / 1000000.0) % 360.0
    beta_deg = sum_b / 1000000.0
    dist_km = 385000.56 + sum_r / 1000.0

    # Equatorial horizontal parallax sin(pi)
    sin_pi = 6378.14 / dist_km

    # Obliquity of the ecliptic and geocentric equatorial (RA, Dec)
    eps_rad = math.radians(
        23.43929111 - 0.013004167 * T_tt - 1.63889e-7 * (T_tt ** 2) + 5.03611e-7 * (T_tt ** 3)
    )
    lam_rad = math.radians(lam_deg)
    beta_rad = math.radians(beta_deg)

    ra_rad = math.atan2(
        math.sin(lam_rad) * math.cos(eps_rad) - math.tan(beta_rad) * math.sin(eps_rad),
        math.cos(lam_rad),
    )
    ra_deg = (math.degrees(ra_rad) + 360.0) % 360.0
    sin_dec = math.sin(beta_rad) * math.cos(eps_rad) + math.cos(beta_rad) * math.sin(eps_rad) * math.sin(lam_rad)
    sin_dec = max(-1.0, min(1.0, sin_dec))
    dec_rad = math.asin(sin_dec)

    # Topocentric horizontal parallax correction (Meeus Ch. 40)
    phi_rad = math.radians(float(lat))
    u = math.atan(0.99664719 * math.tan(phi_rad))
    # Use typical alpine elevation (~2500 m) for minor geoid height term
    elev_ratio = 2500.0 / 6378140.0
    rho_sin_phi_p = 0.99664719 * math.sin(u) + elev_ratio * math.sin(phi_rad)
    rho_cos_phi_p = math.cos(u) + elev_ratio * math.cos(phi_rad)

    # Greenwich Sidereal Time (degrees)
    gmst_deg = (
        280.46061837
        + 360.98564736629 * utc_days
        + 0.000387933 * (T ** 2)
        - (T ** 3) / 38710000.0
    ) % 360.0
    H_deg = (gmst_deg + float(lon) - ra_deg) % 360.0
    H_rad = math.radians(H_deg)

    denom = math.cos(dec_rad) - rho_cos_phi_p * sin_pi * math.cos(H_rad)
    delta_ra_rad = math.atan2(-rho_cos_phi_p * sin_pi * math.sin(H_rad), denom)
    H_topo_rad = H_rad - delta_ra_rad
    dec_topo_rad = math.atan2(
        (math.sin(dec_rad) - rho_sin_phi_p * sin_pi) * math.cos(delta_ra_rad),
        denom,
    )

    sin_alt_topo = (
        math.sin(phi_rad) * math.sin(dec_topo_rad)
        + math.cos(phi_rad) * math.cos(dec_topo_rad) * math.cos(H_topo_rad)
    )
    sin_alt_topo = max(-1.0, min(1.0, sin_alt_topo))
    lunar_alt_deg = math.degrees(math.asin(sin_alt_topo))

    az_y = math.sin(H_topo_rad)
    az_x = math.cos(H_topo_rad) * math.sin(phi_rad) - math.tan(dec_topo_rad) * math.cos(phi_rad)
    lunar_az_deg = (math.degrees(math.atan2(az_y, az_x)) + 180.0) % 360.0

    # Illuminated fraction and phase name (Meeus Ch. 48)
    sun_L0 = (280.46646 + 36000.76983 * T_tt + 0.0003032 * (T_tt ** 2)) % 360.0
    M_rad = math.radians(M)
    sun_C = (
        (1.914602 - 0.004817 * T_tt - 0.000014 * (T_tt ** 2)) * math.sin(M_rad)
        + (0.019993 - 0.000101 * T_tt) * math.sin(2.0 * M_rad)
        + 0.000289 * math.sin(3.0 * M_rad)
    )
    sun_lam_deg = (sun_L0 + sun_C) % 360.0
    synodic_elong_deg = (lam_deg - sun_lam_deg) % 360.0

    cos_psi = math.cos(beta_rad) * math.cos(math.radians(synodic_elong_deg))
    cos_psi = max(-1.0, min(1.0, cos_psi))
    sin_psi = math.sqrt(max(0.0, 1.0 - cos_psi * cos_psi))
    sun_dist_km = 149598000.0
    phase_angle_rad = math.atan2(sun_dist_km * sin_psi, dist_km - sun_dist_km * cos_psi)
    illum_frac = (1.0 + math.cos(phase_angle_rad)) / 2.0
    illum_frac = max(0.0, min(1.0, illum_frac))

    if synodic_elong_deg < 6.0 or synodic_elong_deg >= 354.0 or illum_frac < 0.015:
        phase_name = "New Moon"
    elif 174.0 <= synodic_elong_deg <= 186.0 or illum_frac > 0.985:
        phase_name = "Full Moon"
    elif synodic_elong_deg < 180.0:
        if 84.0 <= synodic_elong_deg <= 96.0:
            phase_name = "First Quarter"
        elif synodic_elong_deg < 90.0:
            phase_name = "Waxing Crescent"
        else:
            phase_name = "Waxing Gibbous"
    else:
        if 264.0 <= synodic_elong_deg <= 276.0:
            phase_name = "Last Quarter"
        elif synodic_elong_deg < 270.0:
            phase_name = "Waning Gibbous"
        else:
            phase_name = "Waning Crescent"

    return {
        "lunar_altitude_deg": round(lunar_alt_deg, 2),
        "lunar_azimuth_deg": round(lunar_az_deg, 2),
        "lunar_cardinal": get_compass_cardinal(lunar_az_deg),
        "lunar_illumination_fraction": round(illum_frac, 3),
        "lunar_phase_name": phase_name,
        "is_moon_above_horizon": bool(lunar_alt_deg > 0.0),
    }


def calculate_solar_position(lat, lon, dt, tz_offset=None, center_az=None):
    """
    Standard NOAA Solar Position Algorithm approximation plus Meeus topocentric lunar ephemeris.
    Returns solar altitude (degrees above horizon), solar azimuth (degrees clockwise from North),
    topocentric lunar position/phase fields (lunar_*), 8-octant flat-ground 2D image-plane shadow lookup,
    and reverse clock-to-camera-bearing lookup.
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
    lunar_info = _calculate_lunar_ephemeris(lat, lon, dt, tz_offset)

    octants = [
        ("N", 0.0),
        ("NE", 45.0),
        ("E", 90.0),
        ("SE", 135.0),
        ("S", 180.0),
        ("SW", 225.0),
        ("W", 270.0),
        ("NW", 315.0),
    ]
    shadow_by_octant = {}
    for oct_name, oct_deg in octants:
        _, desc = _shadow_2d_direction_for_camera(shadow_azimuth, oct_deg)
        shadow_by_octant[f"{oct_name} ({int(oct_deg)} deg)"] = desc

    clock_sectors = [
        ("12 o'clock (away / up)", 0.0),
        ("1-2 o'clock (up-right, away)", 45.0),
        ("3 o'clock (left-to-right)", 90.0),
        ("4-5 o'clock (down-right, toward camera)", 135.0),
        ("6 o'clock (toward camera / down)", 180.0),
        ("7-8 o'clock (down-left, toward camera)", -135.0),
        ("9 o'clock (right-to-left)", -90.0),
        ("10-11 o'clock (up-left, away)", -45.0),
    ]
    clock_to_bearing = {}
    for clk_label, rel_offset in clock_sectors:
        cam_bearing = (shadow_azimuth - rel_offset) % 360.0
        low_b = (cam_bearing - 30.0) % 360.0
        high_b = (cam_bearing + 30.0) % 360.0
        clock_to_bearing[clk_label] = (
            f"~{cam_bearing:.0f} deg ({get_compass_cardinal(cam_bearing)}, "
            f"flat-ground range {low_b:.0f}-{high_b:.0f} deg)"
        )

    res = {
        "solar_altitude_deg": round(solar_altitude_deg, 1),
        "solar_azimuth_deg": round(solar_azimuth, 1),
        "solar_cardinal": get_compass_cardinal(solar_azimuth),
        "shadow_azimuth_deg": round(shadow_azimuth, 1),
        "shadow_cardinal": get_compass_cardinal(shadow_azimuth),
        "lunar_altitude_deg": lunar_info["lunar_altitude_deg"],
        "lunar_azimuth_deg": lunar_info["lunar_azimuth_deg"],
        "lunar_cardinal": lunar_info["lunar_cardinal"],
        "lunar_illumination_fraction": lunar_info["lunar_illumination_fraction"],
        "lunar_phase_name": lunar_info["lunar_phase_name"],
        "is_moon_above_horizon": lunar_info["is_moon_above_horizon"],
        "tz_offset_hours": tz_offset,
        "flat_ground_2d_shadow_by_camera_octant": shadow_by_octant,
        "observed_2d_shadow_clock_to_camera_bearing": clock_to_bearing,
        "terrain_slope_caveat": (
            "WARNING: On 20-35 deg sloped hillsides or near wide-angle frame edges, "
            "2D ground shadows rotate by up to +/-25-35 deg relative to flat-ground clock geometry. "
            "Always prioritize distant horizon/ridge geometry over foreground hillside shadows when they conflict by <=35 deg."
        ),
    }
    if center_az is not None:
        rel_deg, desc = _shadow_2d_direction_for_camera(shadow_azimuth, center_az)
        res["camera_center_azimuth_deg"] = round(center_az % 360.0, 1)
        res["expected_2d_shadow_for_center_az"] = {
            "relative_shadow_angle_deg": rel_deg,
            "flat_ground_2d_shadow_direction": desc,
        }
    return res


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

    def sample_m_array(self, lats, lons):
        """Vectorized bilinear interpolation across 1D/2D NumPy arrays of latitudes and longitudes."""
        gx = (lons - self.min_lon) / (self.max_lon - self.min_lon) * (self.width - 1)
        gy = (self.max_lat - lats) / (self.max_lat - self.min_lat) * (self.height - 1)
        valid = (gx >= 0) & (gx < self.width - 1) & (gy >= 0) & (gy < self.height - 1)
        x0 = np.clip(gx.astype(np.int32), 0, self.width - 2)
        y0 = np.clip(gy.astype(np.int32), 0, self.height - 2)
        dx = gx - x0
        dy = gy - y0
        g00 = self.arr_m[y0, x0]
        g10 = self.arr_m[y0, x0 + 1]
        g01 = self.arr_m[y0 + 1, x0]
        g11 = self.arr_m[y0 + 1, x0 + 1]
        elevs = (1.0 - dx) * (1.0 - dy) * g00 + dx * (1.0 - dy) * g10 + (1.0 - dx) * dy * g01 + dx * dy * g11
        return np.where(valid & np.isfinite(elevs) & (elevs > -500.0), elevs, np.nan)

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
    ArcGIS exportImage never stretches the aspect ratio, and quantizes eff_radius_km to prevent
    cache-key busting across subcommands.
    """
    if not HAS_RASTER_LIBS:
        return None

    # Snap center to 0.10 deg (~11 km) grid and quantize eff_radius_km so all standard radii hit one cached file
    snap_lat = round(lat * 10.0) / 10.0
    snap_lon = round(lon * 10.0) / 10.0
    eff_radius_km = 32.0 if radius_km <= 25.0 else math.ceil((float(radius_km) + 8.0) / 10.0) * 10.0

    d_deg = round(eff_radius_km / (111.0 * math.cos(math.radians(snap_lat))), 4)
    min_lat, max_lat = round(snap_lat - d_deg, 4), round(snap_lat + d_deg, 4)
    min_lon, max_lon = round(snap_lon - d_deg, 4), round(snap_lon + d_deg, 4)

    grid_size = 1100 if eff_radius_km <= 32.0 else min(1800, int(round(1100 * eff_radius_km / 32.0)))
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
                raw_bytes = _fetch_url_bytes(url, timeout=12.0)
                if raw_bytes and len(raw_bytes) > 1024:
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


def query_epqs_point(lat, lon, use_dem=True):
    """Query USGS 3DEP elevation (first via local cached 3DEP DEM raster when use_dem=True, falling back to EPQS HTTP API)."""
    if use_dem:
        dem = get_dem_patch(lat, lon, radius_km=20.0)
        if dem is not None:
            ft_val, m_val = dem.sample_ft_m(lat, lon)
            if ft_val is not None:
                return ft_val, m_val

    url = f"https://epqs.nationalmap.gov/v1/json?x={lon}&y={lat}&wkid=4326&units=Feet&includeDate=false"
    for _ in range(2):
        try:
            raw_bytes = _fetch_url_bytes(url, timeout=6.0)
            res_data = json.loads(raw_bytes.decode("utf-8"))
            val = res_data.get("value")
            if val is not None and val not in (-999999, -1000000) and str(val) not in ("-999999", "-1000000"):
                elev_ft = round(float(val))
                elev_m = round(elev_ft * 0.3048)
                return elev_ft, elev_m
        except Exception:
            continue
    return None, None


_MEM_ADMIN_CACHE = {}


def query_admin_boundaries(lat, lon, timeout=3.0):
    """
    Resolves County, State, and overlapping Federal Land units (National Park, Wilderness Area,
    National Forest) for (lat, lon) with a strict timeout.
    Cached in %TEMP% keyed by (round(lat, 4), round(lon, 4)) (~11m precision) so Sierra Crest
    pass boundary crossings are never misclassified.
    """
    rlat, rlon = round(float(lat), 4), round(float(lon), 4)
    cache_key = f"{rlat:.4f}_{rlon:.4f}"
    if cache_key in _MEM_ADMIN_CACHE:
        return _MEM_ADMIN_CACHE[cache_key]

    key_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()[:14]
    cache_path = os.path.join(tempfile.gettempdir(), f"photo_analysis_admin_v1_{key_hash}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and cached.get("county"):
                _MEM_ADMIN_CACHE[cache_key] = cached
                return cached
        except Exception:
            pass

    # Query USGS National Map govunits layers in a single /identify call:
    # Layer 23: County or Equivalent, Layer 29: National Park, Layer 30: National Monument,
    # Layer 31: National Forest, Layer 32: National Wilderness
    params = {
        "geometry": f"{lon:.6f},{lat:.6f}",
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "layers": "all:23,29,30,31,32",
        "tolerance": "0",
        "mapExtent": f"{lon - 0.05:.4f},{lat - 0.05:.4f},{lon + 0.05:.4f},{lat + 0.05:.4f}",
        "imageDisplay": "100,100,96",
        "returnGeometry": "false",
        "f": "json",
    }
    identify_url = "https://carto.nationalmap.gov/arcgis/rest/services/govunits/MapServer/identify?" + urllib.parse.urlencode(params)
    id_results = []
    any_success = False
    try:
        raw = _fetch_url_bytes(identify_url, timeout=timeout)
        id_results = json.loads(raw.decode("utf-8")).get("results", [])
        any_success = True
    except Exception:
        id_results = []

    state_name = None
    state_code = None
    county_name = None
    national_parks = []
    wilderness_areas = []
    national_forests = []

    for r in id_results:
        lid = r.get("layerId")
        attr = r.get("attributes") or {}
        attr_ci = {str(k).upper(): v for k, v in attr.items() if v not in (None, "", "Null")}
        if lid == 23:
            county_name = county_name or attr_ci.get("COUNTY_NAME") or attr_ci.get("GNIS_NAME") or attr_ci.get("NAME")
            state_name = state_name or attr_ci.get("STATE_NAME")
            state_code = state_code or attr_ci.get("STUSPS")
        elif lid in (29, 30):
            nm = attr_ci.get("UNIT_NAME") or attr_ci.get("NAME")
            if nm and nm not in national_parks:
                national_parks.append(nm)
        elif lid == 31:
            nm = attr_ci.get("FORESTNAME") or attr_ci.get("ADMINFORESTNAME") or attr_ci.get("NAME")
            if nm and nm not in national_forests:
                national_forests.append(nm)
        elif lid == 32:
            nm = attr_ci.get("WILDERNESSNAME") or attr_ci.get("NAME")
            if nm and nm not in wilderness_areas:
                wilderness_areas.append(nm)

    federal_lands = []
    for lst in (wilderness_areas, national_parks, national_forests):
        for item in lst:
            if item not in federal_lands:
                federal_lands.append(item)

    loc_parts = []
    if federal_lands:
        loc_parts.append(" / ".join(federal_lands))
    if county_name:
        c_str = county_name if "county" in county_name.lower() else f"{county_name} County"
        loc_parts.append(c_str)
    if state_name or state_code:
        loc_parts.append(state_name or state_code)

    out = {
        "state": state_name or state_code,
        "state_code": state_code,
        "county": county_name,
        "national_park": national_parks[0] if national_parks else None,
        "wilderness": wilderness_areas[0] if wilderness_areas else None,
        "national_forest": national_forests[0] if national_forests else None,
        "federal_lands": federal_lands,
        "location_formatted": ", ".join(loc_parts) if loc_parts else None,
    }

    if any_success and (county_name or federal_lands):
        _MEM_ADMIN_CACHE[cache_key] = out
        try:
            tmp_path = f"{cache_path}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(out, f)
            os.replace(tmp_path, cache_path)
        except Exception:
            pass

    return out


def _extract_arcgis_lon_lat(geom):
    """
    Extracts (lon, lat) from ArcGIS point ('x', 'y'), multipoint ('points'),
    or polygon ('rings') geometry.
    """
    if not isinstance(geom, dict):
        return None, None
    if "x" in geom and "y" in geom and geom["x"] is not None and geom["y"] is not None:
        return float(geom["x"]), float(geom["y"])
    pts = geom.get("points")
    if pts and isinstance(pts, list) and len(pts) > 0 and len(pts[0]) >= 2:
        return float(pts[0][0]), float(pts[0][1])
    rings = geom.get("rings")
    if rings and isinstance(rings, list) and len(rings) > 0:
        ring = rings[0]
        if ring and len(ring) > 0:
            pts_ring = ring[:-1] if (len(ring) > 1 and ring[0] == ring[-1]) else ring
            if pts_ring:
                lon = sum(float(p[0]) for p in pts_ring) / len(pts_ring)
                lat = sum(float(p[1]) for p in pts_ring) / len(pts_ring)
                return lon, lat
    return None, None


def query_usgs_gnis(lat, lon, radius_km=12.0, _return_status=False):
    """
    Queries USGS Geographic Names Information System (GNIS) ArcGIS REST service.
    Layer 5: Landforms (Summit, Ridge, Gap, Valley, Basin, Cliff, Pillar, Flat, Range)
    Layer 6: Streams (Mouth)
    Layer 7: Other Hydrographic Features (Lake, Falls, Glacier, Swamp)
    Supports point ('x', 'y'), multipoint ('points'), and polygon ('rings') geometries.
    """
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    min_lat, max_lat = lat - dlat, lat + dlat
    min_lon, max_lon = lon - dlon, lon + dlon
    had_error = [False]

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
        try:
            raw_bytes = _fetch_url_bytes(url, timeout=9.0)
            data = json.loads(raw_bytes.decode("utf-8"))
            if "error" in data:
                had_error[0] = True
                sys.stderr.write(f"USGS GNIS layer {layer_id} returned error: {data['error']}\n")
                return []
            return data.get("features", [])
        except Exception as e:
            had_error[0] = True
            sys.stderr.write(f"USGS GNIS layer {layer_id} query failed: {e}\n")
            return []

    features = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for layer_items in pool.map(fetch_layer, (5, 6, 7)):
            for item in layer_items:
                attr = item.get("attributes", {})
                geom = item.get("geometry", {})
                f_lon, f_lat = _extract_arcgis_lon_lat(geom)
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

    if _return_status:
        return features, (not had_error[0])
    return features


def query_usgs_waterbodies(lat, lon, radius_km=12.0, unnamed_radius_km=2.5, _return_status=False):
    """
    Queries USGS National Hydrography Dataset (NHD) MapServer Layer 12 (NHDWaterbody) for:
    1. Named lakes/waterbodies (GNIS_NAME IS NOT NULL) across the full radius_km.
    2. Unnamed alpine tarns/waterbodies within unnamed_radius_km (default 2.5 km around grid center),
       restricted by FType/FCODE to LakePond (FType=390 or FCODE IN (39000,39004,39009,39010,39011,39012))
       and Reservoir (FType=436), excluding SwampMarsh (466), Ice Mass (378), and tiny seasonal puddles
       (AreaSqKm < 0.0005 when AreaSqKm is populated).
    """
    nhd_url = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12/query"
    had_error = [False]
    valid_unnamed_fcodes = {39000, 39004, 39009, 39010, 39011, 39012}

    def fetch_named():
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
        env = f"{lon - dlon:.4f},{lat - dlat:.4f},{lon + dlon:.4f},{lat + dlat:.4f}"
        params = {
            "geometry": env,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "where": "GNIS_NAME IS NOT NULL",
            "outFields": "GNIS_NAME,FTYPE,FCODE,AREASQKM,ELEVATION",
            "returnGeometry": "true",
            "maxAllowableOffset": "0.0002",
            "outSR": "4326",
            "f": "json",
        }
        try:
            raw_bytes = _fetch_url_bytes(nhd_url + "?" + urllib.parse.urlencode(params), timeout=8.5)
            data = json.loads(raw_bytes.decode("utf-8"))
            if "error" in data:
                had_error[0] = True
                sys.stderr.write(f"USGS NHD named waterbodies query error: {data['error']}\n")
                return []
            return data.get("features", [])
        except Exception as e:
            had_error[0] = True
            sys.stderr.write(f"USGS NHD named waterbodies query failed: {e}\n")
            return []

    def fetch_unnamed():
        u_rad = min(float(radius_km), float(unnamed_radius_km))
        dlat = u_rad / 111.0
        dlon = u_rad / (111.0 * math.cos(math.radians(lat)))
        env = f"{lon - dlon:.4f},{lat - dlat:.4f},{lon + dlon:.4f},{lat + dlat:.4f}"
        params = {
            "geometry": env,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "where": "GNIS_NAME IS NULL AND (FTYPE IN (390, 436) OR FCODE IN (39000, 39004, 39009, 39010, 39011, 39012))",
            "outFields": "GNIS_NAME,FTYPE,FCODE,AREASQKM,ELEVATION",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        }
        try:
            raw_bytes = _fetch_url_bytes(nhd_url + "?" + urllib.parse.urlencode(params), timeout=8.0)
            data = json.loads(raw_bytes.decode("utf-8"))
            if "error" in data:
                had_error[0] = True
                sys.stderr.write(f"USGS NHD unnamed waterbodies query error: {data['error']}\n")
                return []
            return data.get("features", [])
        except Exception as e:
            had_error[0] = True
            sys.stderr.write(f"USGS NHD unnamed waterbodies query failed: {e}\n")
            return []

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_named = pool.submit(fetch_named)
        fut_unnamed = pool.submit(fetch_unnamed)
        named_items = fut_named.result()
        unnamed_items = fut_unnamed.result()

    named_by_key = {}
    for item in named_items:
        attr = item.get("attributes") or {}
        attr_ci = {str(k).upper(): v for k, v in attr.items()}
        name = (attr_ci.get("GNIS_NAME") or "").strip()
        if not name:
            continue
        ftype = attr_ci.get("FTYPE")
        fcode = attr_ci.get("FCODE")
        try:
            ftype = int(ftype) if ftype is not None else None
        except (ValueError, TypeError):
            ftype = None
        try:
            fcode = int(fcode) if fcode is not None else None
        except (ValueError, TypeError):
            fcode = None

        # Exclude SwampMarsh (466)
        if ftype == 466 or (fcode is not None and 46600 <= fcode < 46700):
            continue
        f_type = "Glacier" if (ftype == 378 or (fcode is not None and 37800 <= fcode < 37900)) else "Lake"

        f_lon, f_lat = _extract_arcgis_lon_lat(item.get("geometry") or {})
        if f_lat is None or f_lon is None:
            continue
        calc = calculate_haversine(lat, lon, f_lat, f_lon)
        if calc["distance_km"] > radius_km:
            continue

        area_val = attr_ci.get("AREASQKM")
        try:
            area_sqkm = float(area_val) if area_val is not None else 0.0
        except (ValueError, TypeError):
            area_sqkm = 0.0

        rec = {
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
            "source": "USGS NHD",
            "_area_sqkm": area_sqkm,
        }
        k = name.lower()
        if k not in named_by_key or area_sqkm > named_by_key[k].get("_area_sqkm", 0.0):
            named_by_key[k] = rec

    features = []
    for rec in named_by_key.values():
        rec.pop("_area_sqkm", None)
        features.append(rec)

    u_rad = min(float(radius_km), float(unnamed_radius_km))
    for item in unnamed_items:
        attr = item.get("attributes") or {}
        attr_ci = {str(k).upper(): v for k, v in attr.items()}
        if (attr_ci.get("GNIS_NAME") or "").strip():
            continue
        ftype = attr_ci.get("FTYPE")
        fcode = attr_ci.get("FCODE")
        try:
            ftype = int(ftype) if ftype is not None else None
        except (ValueError, TypeError):
            ftype = None
        try:
            fcode = int(fcode) if fcode is not None else None
        except (ValueError, TypeError):
            fcode = None

        # Restrict unnamed NHD waterbodies to LakePond (FType=390 or FCODE in valid_unnamed_fcodes) and Reservoir (FType=436)
        if ftype in (466, 378):
            continue
        if not (ftype in (390, 436) or (fcode in valid_unnamed_fcodes)):
            continue

        area_val = attr_ci.get("AREASQKM")
        if area_val is not None:
            try:
                if float(area_val) < 0.0005:
                    continue
            except (ValueError, TypeError):
                pass

        f_lon, f_lat = _extract_arcgis_lon_lat(item.get("geometry") or {})
        if f_lat is None or f_lon is None:
            continue
        calc = calculate_haversine(lat, lon, f_lat, f_lon)
        if calc["distance_km"] > u_rad:
            continue

        features.append({
            "name": f"Unnamed Tarn / Water Body ({f_lat:.4f}, {f_lon:.4f})",
            "type": "Lake",
            "distance_mi": calc["distance_mi"],
            "distance_km": calc["distance_km"],
            "bearing_deg": calc["bearing_deg"],
            "cardinal": calc["cardinal"],
            "elevation_ft": None,
            "elevation_m": None,
            "lat": round(f_lat, 6),
            "lon": round(f_lon, 6),
            "source": "USGS NHD",
            "is_unnamed_water": True,
        })

    if _return_status:
        return features, (not had_error[0])
    return features


def query_overpass_peaks(lat, lon, radius_km=10.0, _return_status=False):
    """
    Queries Overpass API via POST with encoded payload to enrich or supplement features,
    including multipolygon water relations within 8 km and unnamed alpine tarns/lakes within 2.5 km.
    Enforces a strict <= 7.0s total wall-clock budget across primary and at most one fast fallback.
    """
    radius_m = int(min(radius_km, 15.0) * 1000)
    water_radius_m = min(radius_m, 8000)
    unnamed_water_m = min(radius_m, 2500)

    dlat = (radius_m / 1000.0) / 111.0
    dlon = (radius_m / 1000.0) / (111.0 * math.cos(math.radians(lat)))
    bbox_str = f"{lat - dlat:.4f},{lon - dlon:.4f},{lat + dlat:.4f},{lon + dlon:.4f}"

    endpoints = [
        ("https://overpass-api.de/api/interpreter", 4, 4.2),
        ("https://overpass.kumi.systems/api/interpreter", 3, 2.8),
    ]
    t_start = time.monotonic()
    res_data = None
    ok = False

    for idx, (endpoint_url, ovp_timeout, req_timeout) in enumerate(endpoints):
        elapsed = time.monotonic() - t_start
        rem_budget = 7.0 - elapsed
        if rem_budget < 1.2:
            break
        eff_req_timeout = min(req_timeout, max(1.2, rem_budget - 0.1))
        eff_ovp_timeout = min(ovp_timeout, max(1, int(eff_req_timeout)))
        query = f"""[out:json][timeout:{eff_ovp_timeout}][bbox:{bbox_str}];
(
  node(around:{radius_m},{lat},{lon})["natural"="peak"]["name"];
  node(around:{radius_m},{lat},{lon})["natural"="water"]["name"];
  node(around:{radius_m},{lat},{lon})["natural"="saddle"]["name"];
  way(around:{water_radius_m},{lat},{lon})["natural"="water"]["name"];
  relation(around:{water_radius_m},{lat},{lon})["natural"="water"]["name"];
  way(around:{unnamed_water_m},{lat},{lon})["natural"="water"][!"name"]["water"!~"river|stream|canal|ditch"];
  relation(around:{unnamed_water_m},{lat},{lon})["natural"="water"][!"name"]["water"!~"river|stream|canal|ditch"];
);
out center tags;
"""
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        try:
            raw_bytes = _fetch_url_bytes(endpoint_url, data=data, timeout=eff_req_timeout)
            parsed = json.loads(raw_bytes.decode("utf-8"))
            if "remark" in parsed:
                sys.stderr.write(f"Overpass runtime remark on {endpoint_url}: {parsed.get('remark')}\n")
                ok = False
                continue
            res_data = parsed
            ok = True
            break
        except Exception as e:
            sys.stderr.write(f"Overpass query failed on {endpoint_url} (attempt {idx + 1}): {e}\n")
            ok = False

    features = []
    if ok and res_data:
        for el in res_data.get("elements", []):
            tags = el.get("tags", {})
            f_lat = el.get("lat") or (el.get("center", {}).get("lat"))
            f_lon = el.get("lon") or (el.get("center", {}).get("lon"))
            if not (f_lat and f_lon):
                continue
            name = tags.get("name")
            is_unnamed_water = False
            if not name:
                if tags.get("natural") == "water":
                    name = f"Unnamed Tarn / Water Body ({f_lat:.4f}, {f_lon:.4f})"
                    is_unnamed_water = True
                else:
                    continue
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
            item = {
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
                "source": "OpenStreetMap",
            }
            if is_unnamed_water:
                item["is_unnamed_water"] = True
            features.append(item)

    if _return_status:
        return features, ok
    return features


_MEM_FEATURES_CACHE = {}


def _merge_feature_catalog(feature_lists):
    """
    Merges raw feature records from GNIS, NHD, and Overpass, deduplicating named features by
    case-insensitive name and spatially deduplicating unnamed water bodies within 100 m (0.10 km).
    """
    by_name = {}
    unnamed_waters = []
    for feat_list in feature_lists:
        for f in (feat_list or []):
            is_unnamed = bool(f.get("is_unnamed_water") or str(f.get("name", "")).startswith("Unnamed Tarn / Water Body"))
            raw_rec = {
                "name": f["name"],
                "type": f["type"],
                "lat": f["lat"],
                "lon": f["lon"],
                "elevation_ft": f.get("elevation_ft"),
                "elevation_m": f.get("elevation_m"),
                "source": f.get("source", "USGS GNIS"),
                "is_unnamed_water": is_unnamed,
            }
            if is_unnamed:
                duplicate_found = False
                for existing_uw in unnamed_waters:
                    d_uw = calculate_haversine(existing_uw["lat"], existing_uw["lon"], raw_rec["lat"], raw_rec["lon"])
                    if d_uw["distance_km"] <= 0.10:
                        if existing_uw.get("elevation_ft") is None and raw_rec.get("elevation_ft") is not None:
                            existing_uw["elevation_ft"] = raw_rec["elevation_ft"]
                            existing_uw["elevation_m"] = raw_rec["elevation_m"]
                        duplicate_found = True
                        break
                if not duplicate_found:
                    unnamed_waters.append(raw_rec)
            else:
                key = f["name"].strip().lower()
                if key not in by_name:
                    by_name[key] = raw_rec
                else:
                    existing = by_name[key]
                    if existing.get("type") == "Swamp" and raw_rec.get("type") == "Lake":
                        existing["type"] = "Lake"
                    if existing.get("elevation_ft") is None and raw_rec.get("elevation_ft") is not None:
                        existing["elevation_ft"] = raw_rec["elevation_ft"]
                        existing["elevation_m"] = raw_rec["elevation_m"]
    return list(by_name.values()) + unnamed_waters


def find_nearby_features(lat, lon, radius_km=12.0, verbose=False):
    """
    Finds nearby geographic features (summits, ridges, gaps, lakes, basins, valleys, streams)
    around (lat, lon). Caches ONLY camera-independent raw feature records in %TEMP% keyed by
    v3_(round(lat, 2), round(lon, 2), quantized_radius_km), reuses any larger-radius cache
    (R >= q_radius_km), tracks provider completeness (gnis_ok, nhd_ok, overpass_ok) to prevent
    cache poisoning, and recomputes exact caller-relative distance and bearing on every call.
    """
    dem = get_dem_patch(lat, lon, radius_km=radius_km)
    grid_lat = round(float(lat), 2)
    grid_lon = round(float(lon), 2)
    q_radius_km = int(math.ceil(max(5.0, float(radius_km)) / 5.0) * 5)
    cache_dir = tempfile.gettempdir()

    candidate_radii = sorted(set([q_radius_km] + [r for r in (15, 20, 25, 30, 35, 40) if r >= q_radius_km]))
    raw_catalog = None
    partial_state = None
    partial_r = q_radius_km

    for cand_r in candidate_radii:
        cand_key = f"v3_{grid_lat:.2f}_{grid_lon:.2f}_{cand_r}"
        cand_hash = hashlib.md5(cand_key.encode("utf-8")).hexdigest()[:14]
        cand_path = os.path.join(cache_dir, f"photo_analysis_features_{cand_key}_{cand_hash}.json")

        loaded = _MEM_FEATURES_CACHE.get(cand_key)
        if loaded is None and os.path.exists(cand_path):
            try:
                with open(cand_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and loaded.get("features"):
                    _MEM_FEATURES_CACHE[cand_key] = loaded
            except Exception:
                loaded = None

        if isinstance(loaded, dict) and loaded.get("features"):
            if loaded.get("gnis_ok") and loaded.get("nhd_ok"):
                raw_catalog = loaded["features"]
                break
            elif partial_state is None and cand_r >= q_radius_km:
                partial_state = loaded
                partial_r = cand_r

    if raw_catalog is None:
        eff_r = partial_r if partial_state is not None else q_radius_km
        cache_key = f"v3_{grid_lat:.2f}_{grid_lon:.2f}_{eff_r}"
        key_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()[:14]
        cache_path = os.path.join(cache_dir, f"photo_analysis_features_{cache_key}_{key_hash}.json")

        # Query with +1.5 km margin around grid center so edge callers within the 0.01 deg cell lose nothing
        search_r = float(eff_r) + 1.5
        need_gnis = not (partial_state and partial_state.get("gnis_ok"))
        need_nhd = not (partial_state and partial_state.get("nhd_ok"))
        need_osm = not (partial_state and partial_state.get("overpass_ok"))

        gnis_features, gnis_ok = ([], True) if not need_gnis else ([], False)
        nhd_features, nhd_ok = ([], True) if not need_nhd else ([], False)
        osm_features, osm_ok = ([], True) if not need_osm else ([], False)

        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_gnis = pool.submit(query_usgs_gnis, grid_lat, grid_lon, search_r, True) if need_gnis else None
            fut_nhd = pool.submit(query_usgs_waterbodies, grid_lat, grid_lon, search_r, 2.5, True) if need_nhd else None
            fut_osm = pool.submit(query_overpass_peaks, grid_lat, grid_lon, min(search_r, 22.0), True) if need_osm else None

            if fut_gnis is not None:
                gnis_features, gnis_ok = fut_gnis.result()
            if fut_nhd is not None:
                nhd_features, nhd_ok = fut_nhd.result()
            if fut_osm is not None:
                osm_features, osm_ok = fut_osm.result()

        prior_features = partial_state.get("features", []) if partial_state else []
        raw_catalog = _merge_feature_catalog([prior_features, gnis_features, nhd_features, osm_features])

        cache_obj = {
            "gnis_ok": bool(gnis_ok),
            "nhd_ok": bool(nhd_ok),
            "overpass_ok": bool(osm_ok),
            "radius_km": eff_r,
            "features": raw_catalog,
        }
        if len(raw_catalog) > 0:
            _MEM_FEATURES_CACHE[cache_key] = cache_obj
            # Only persist to disk when both USGS GNIS (landforms) and USGS NHD (lakes/tarns) succeeded
            if gnis_ok and nhd_ok:
                try:
                    tmp_path = f"{cache_path}.tmp.{os.getpid()}"
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(cache_obj, f)
                    os.replace(tmp_path, cache_path)
                except Exception:
                    pass

    # Recompute exact caller-relative distance and bearing on every invocation,
    # and enforce 100 m (0.10 km) spatial proximity deduplication on unnamed water bodies
    caller_features = []
    seen_unnamed_coords = []
    for raw in raw_catalog:
        calc = calculate_haversine(lat, lon, raw["lat"], raw["lon"])
        if calc["distance_km"] > radius_km:
            continue
        is_unnamed_water = bool(raw.get("is_unnamed_water") or str(raw.get("name", "")).startswith("Unnamed Tarn / Water Body"))
        if is_unnamed_water:
            if calc["distance_km"] > 1.5:
                continue
            if any(calculate_haversine(u_lat, u_lon, raw["lat"], raw["lon"])["distance_km"] <= 0.10 for u_lat, u_lon in seen_unnamed_coords):
                continue
            seen_unnamed_coords.append((raw["lat"], raw["lon"]))
        if not verbose and raw.get("type") == "Stream" and calc["distance_km"] > 5.0:
            continue
        caller_features.append({
            "name": raw["name"],
            "type": raw["type"],
            "distance_mi": calc["distance_mi"],
            "distance_km": calc["distance_km"],
            "bearing_deg": calc["bearing_deg"],
            "cardinal": calc["cardinal"],
            "elevation_ft": raw.get("elevation_ft"),
            "elevation_m": raw.get("elevation_m"),
            "lat": raw["lat"],
            "lon": raw["lon"],
            "source": raw.get("source", "USGS GNIS"),
            "_is_unnamed_water": is_unnamed_water,
        })

    caller_features.sort(key=lambda x: x["distance_km"])

    # Cap unnamed water bodies to the 5 closest within 1.5 km
    filtered_features = []
    unnamed_water_count = 0
    for item in caller_features:
        if item.pop("_is_unnamed_water", False):
            if unnamed_water_count >= 5:
                continue
            unnamed_water_count += 1
            item["_needs_water_elev_suffix"] = True
        filtered_features.append(item)

    unique_features = filtered_features

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
            if item.pop("_needs_water_elev_suffix", False) and item.get("elevation_ft") is not None:
                if "~" not in item["name"]:
                    item["name"] = f"{item['name']} ~{item['elevation_ft']:,} ft"
    else:
        for item in unique_features:
            item.pop("_needs_water_elev_suffix", None)

    # Fallback to EPQS only for any remaining top features outside the DEM patch
    missing_elev = [
        f for f in unique_features
        if f.get("elevation_ft") is None and f.get("type") in ("Summit", "Lake", "Gap", "Saddle", "Ridge", "Basin")
    ][:8]
    if missing_elev:
        with ThreadPoolExecutor(max_workers=4) as pool:
            elevs = list(pool.map(lambda item: query_epqs_point(item["lat"], item["lon"], use_dem=False), missing_elev))
        for item, (e_ft, e_m) in zip(missing_elev, elevs):
            item["elevation_ft"] = e_ft
            item["elevation_m"] = e_m

    return unique_features


def compute_elevation_profile(
    lat1,
    lon1,
    lat2,
    lon2,
    samples=40,
    target_elev_ft=None,
    target_elev_m=None,
    dem=None,
    include_profile=True,
):
    """
    Computes a topographic elevation profile and line-of-sight occlusion check between
    camera (lat1, lon1) and target (lat2, lon2) using cached USGS 3DEP DEM raster (with EPQS fallback).
    Unifies observer height (eye_m = cam_m + 1.7) and Earth curvature + refraction correction
    with compute_horizon_skyline and render_synthetic_pano.
    Never returns a false 'VISIBLE' if endpoint or near-camera canyon-wall elevations are missing.
    """
    total_dist = calculate_haversine(lat1, lon1, lat2, lon2)
    req_radius_km = max(20.0, total_dist["distance_km"] + 4.0)

    if dem is None or not dem.contains(lat1, lon1):
        dem = get_dem_patch(lat1, lon1, radius_km=req_radius_km)
    elif not dem.contains(lat2, lon2):
        bigger_dem = get_dem_patch(lat1, lon1, radius_km=req_radius_km)
        if bigger_dem is not None:
            dem = bigger_dem

    if dem is not None and dem.contains(lat1, lon1):
        samples = max(20, min(120, samples))
        coords = [
            (
                lat1 + (lat2 - lat1) * i / samples,
                lon1 + (lon2 - lon1) * i / samples,
            )
            for i in range(samples + 1)
        ]
        elevs = []
        raw_m_list = []
        outside_indices = []
        for idx, (p_lat, p_lon) in enumerate(coords):
            if dem.contains(p_lat, p_lon, margin_deg=0.0):
                m_val = dem.sample_m(p_lat, p_lon)
                if m_val is not None:
                    elevs.append((round(m_val * 3.280839895), round(m_val)))
                    raw_m_list.append(float(m_val))
                    continue
            elevs.append((None, None))
            raw_m_list.append(None)
            outside_indices.append(idx)

        # If target or some outer points lie outside the DEM patch, query EPQS for up to 8 points without discarding local DEM
        if outside_indices:
            if len(outside_indices) > 8:
                picked = sorted(set([outside_indices[0], outside_indices[-1]] + [
                    outside_indices[int(round(j * (len(outside_indices) - 1) / 6.0))] for j in range(1, 6)
                ]))
            else:
                picked = outside_indices
            with ThreadPoolExecutor(max_workers=4) as pool:
                epqs_res = list(pool.map(lambda idx: query_epqs_point(coords[idx][0], coords[idx][1], use_dem=False), picked))
            for idx, (e_ft, e_m) in zip(picked, epqs_res):
                elevs[idx] = (e_ft, e_m)
                raw_m_list[idx] = float(e_ft) * 0.3048 if e_ft is not None else None

        if target_elev_ft is not None:
            t_m = target_elev_m if target_elev_m is not None else round(target_elev_ft * 0.3048)
            elevs[-1] = (target_elev_ft, t_m)
            raw_m_list[-1] = float(target_elev_ft) * 0.3048
        elif dem.contains(lat2, lon2, margin_deg=0.0):
            pk_ft, pk_m = dem.sample_peak_ft_m(lat2, lon2)
            if pk_ft is not None and (elevs[-1][0] is None or pk_ft > elevs[-1][0]):
                elevs[-1] = (pk_ft, pk_m)
                raw_m_list[-1] = float(pk_ft) * 0.3048
    else:
        samples = max(8, min(16, samples))
        coords = [
            (
                lat1 + (lat2 - lat1) * i / samples,
                lon1 + (lon2 - lon1) * i / samples,
            )
            for i in range(samples + 1)
        ]
        # Cap EPQS fallback to <= 8 network points
        if len(coords) > 8:
            picked = sorted(set([0, len(coords) - 1] + [
                int(round(j * (len(coords) - 1) / 6.0)) for j in range(1, 6)
            ]))
        else:
            picked = list(range(len(coords)))
        elevs = [(None, None)] * len(coords)
        raw_m_list = [None] * len(coords)
        with ThreadPoolExecutor(max_workers=4) as pool:
            epqs_res = list(pool.map(lambda idx: query_epqs_point(coords[idx][0], coords[idx][1], use_dem=False), picked))
        for idx, (e_ft, e_m) in zip(picked, epqs_res):
            elevs[idx] = (e_ft, e_m)
            raw_m_list[idx] = float(e_ft) * 0.3048 if e_ft is not None else None
        if target_elev_ft is not None:
            t_m = target_elev_m if target_elev_m is not None else round(target_elev_ft * 0.3048)
            elevs[-1] = (target_elev_ft, t_m)
            raw_m_list[-1] = float(target_elev_ft) * 0.3048

    cam_ft, cam_m = elevs[0]
    tgt_ft, tgt_m = elevs[-1]
    cam_m_exact = raw_m_list[0] if raw_m_list[0] is not None else (cam_ft * 0.3048 if cam_ft is not None else None)
    # Unified 1.7 m (5.58 ft) observer eye height above ground and Earth curvature + refraction drop
    eye_m = (cam_m_exact + 1.7) if cam_m_exact is not None else None
    r_eff = 6371000.0 / (1.0 - 0.13)

    steps = []
    max_intervening_angle = -90.0
    occluding_step = None
    missing_intermediate_count = 0
    near_camera_missing = False

    for i, ((p_lat, p_lon), (e_ft, e_m), e_m_exact) in enumerate(zip(coords, elevs, raw_m_list)):
        d = calculate_haversine(lat1, lon1, p_lat, p_lon) if i > 0 else {
            "distance_m": 0.0, "distance_ft": 0.0, "distance_mi": 0.0, "distance_km": 0.0
        }
        if 0 < i < samples and e_ft is None:
            missing_intermediate_count += 1
            if i <= min(4, samples // 2):
                near_camera_missing = True

        vert_angle = None
        if i > 0 and eye_m is not None and e_m_exact is not None and d["distance_m"] > 0:
            drop_m = (d["distance_m"] ** 2) / (2.0 * r_eff)
            apparent_h = e_m_exact - drop_m
            vert_angle = round(math.degrees(math.atan2(apparent_h - eye_m, d["distance_m"])), 2)
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

    # Compute distinct intervening ridge (excluding points on the target mountain's own monotonic final summit slope)
    total_dist_km = total_dist["distance_km"]
    max_distinct_intervening_angle = -90.0
    highest_intervening_ridge = None
    for i in range(1, samples):
        st = steps[i]
        v_ang = st["vertical_angle_deg"]
        e_m_i = raw_m_list[i]
        if v_ang is None or e_m_i is None:
            continue
        subsequent_elevs = [raw_m_list[k] for k in range(i + 1, samples) if raw_m_list[k] is not None]
        subsequent_angs = [steps[k]["vertical_angle_deg"] for k in range(i + 1, samples + 1) if steps[k]["vertical_angle_deg"] is not None]
        has_saddle_drop = bool(subsequent_elevs and (e_m_i - min(subsequent_elevs)) >= 15.0)
        has_angle_dip_within_85pct = bool(
            total_dist_km > 0.0
            and st["distance_km"] <= 0.85 * total_dist_km
            and subsequent_angs
            and min(subsequent_angs) < (v_ang - 0.02)
        )
        if has_saddle_drop or has_angle_dip_within_85pct:
            if v_ang > max_distinct_intervening_angle:
                max_distinct_intervening_angle = v_ang
                highest_intervening_ridge = {
                    "step": st["step"],
                    "lat": st["lat"],
                    "lon": st["lon"],
                    "distance_mi": st["distance_mi"],
                    "distance_km": st["distance_km"],
                    "elevation_ft": st["elevation_ft"],
                    "elevation_m": st["elevation_m"],
                    "vertical_angle_deg": v_ang,
                }

    target_angle = steps[-1]["vertical_angle_deg"]
    is_occluded = (
        target_angle is not None
        and occluding_step is not None
        and max_intervening_angle > target_angle + 0.15
    )

    if is_occluded and highest_intervening_ridge is None:
        highest_intervening_ridge = occluding_step
        max_distinct_intervening_angle = max_intervening_angle

    if is_occluded:
        los_status = "OCCLUDED"
    elif cam_ft is None or tgt_ft is None or target_angle is None:
        los_status = "UNKNOWN (INCOMPLETE_ELEVATION_DATA)"
    elif near_camera_missing or (samples > 2 and missing_intermediate_count / float(samples - 1) > 0.25):
        los_status = "UNKNOWN (INCOMPLETE_ELEVATION_DATA)"
    else:
        los_status = "VISIBLE"

    reported_intervening_deg = (
        round(max_intervening_angle, 2)
        if is_occluded
        else (round(max_distinct_intervening_angle, 2) if highest_intervening_ridge is not None else None)
    )
    clearance_deg = (
        round(target_angle - reported_intervening_deg, 2)
        if (target_angle is not None and reported_intervening_deg is not None)
        else None
    )

    visibility_class = "UNKNOWN"
    visibility_note = None
    if los_status == "VISIBLE":
        if (
            clearance_deg is not None
            and 0.0 <= clearance_deg < 0.25
            and highest_intervening_ridge is not None
            and total_dist_km > 0.0
            and highest_intervening_ridge["distance_km"] <= 0.75 * total_dist_km
        ):
            visibility_class = "GRAZING_TIP_ONLY"
            visibility_note = (
                f"GRAZING TIP ONLY: clears intervening ridge at "
                f"{highest_intervening_ridge['distance_mi']} mi ({highest_intervening_ridge['elevation_ft']} ft) "
                f"by +{clearance_deg:.2f} deg"
            )
        else:
            visibility_class = "CLEARLY_VISIBLE"
    elif los_status == "OCCLUDED":
        visibility_class = "OCCLUDED"
        if occluding_step is not None and target_angle is not None:
            visibility_note = (
                f"OCCLUDED by ridge at {occluding_step['distance_mi']} mi "
                f"({occluding_step['elevation_ft']} ft, {occluding_step['vertical_angle_deg']:+.2f} deg "
                f"vs target {target_angle:+.2f} deg)"
            )

    res = {
        "bearing_deg": total_dist["bearing_deg"],
        "cardinal": total_dist["cardinal"],
        "distance_mi": total_dist["distance_mi"],
        "distance_km": total_dist["distance_km"],
        "total_distance_mi": total_dist["distance_mi"],
        "total_distance_km": total_dist["distance_km"],
        "target_lat": round(float(lat2), 6),
        "target_lon": round(float(lon2), 6),
        "camera_elevation_ft": cam_ft,
        "camera_elevation_m": cam_m,
        "target_elevation_ft": tgt_ft,
        "target_elevation_m": tgt_m,
        "target_vertical_angle_deg": target_angle,
        "max_intervening_angle_deg": reported_intervening_deg,
        "clearance_deg": clearance_deg,
        "line_of_sight": los_status,
        "visibility_class": visibility_class,
        "visibility_note": visibility_note,
        "highest_intervening_ridge": highest_intervening_ridge,
        "occluding_ridge": occluding_step if is_occluded else None,
        "occluded_by": occluding_step if is_occluded else None,
    }
    if include_profile:
        res["profile"] = steps
    return res


def compute_horizon_skyline(
    lat,
    lon,
    radius_km=22.0,
    az_min=0.0,
    az_max=360.0,
    az_step=1.0,
    center_az=None,
    hfov_deg=54.4,
    projection="auto",
    check_coords=None,
    image_width=None,
):
    """
    Computes:
    1. True topographic DEM horizon profile across [az_min, az_max] (or around center_az +/- (hfov/2 + 20 deg)),
       extracting local horizon highpoints (including UNNAMED peaks/ridges absent from GNIS/OSM) and
       near-field foreground ridges (50m-500m).
    2. Line-of-sight visibility, grazing-tip detection, extended-landform centroid classification,
       and rectilinear/cylindrical FOV pixel-X projection for all named GNIS/OSM features and --check-coords.
    3. Top 10 dominant visible summits across all 360 degrees.
    """
    dem = get_dem_patch(lat, lon, radius_km=radius_km)
    cam_ft, cam_m = (dem.sample_ft_m(lat, lon) if dem is not None else (None, None))
    if cam_ft is None:
        cam_ft, cam_m = query_epqs_point(lat, lon, use_dem=False)
    cam_m_exact = (dem.sample_m(lat, lon) if dem is not None else None)
    if cam_m_exact is None and cam_ft is not None:
        cam_m_exact = cam_ft * 0.3048
    eye_m = (cam_m_exact + 1.7) if cam_m_exact is not None else None
    r_eff = 6371000.0 / (1.0 - 0.13)

    sector_span = (az_max - az_min) % 360.0
    has_explicit_sector = 0.0 < sector_span < 360.0

    eff_center_az = center_az
    eff_hfov_deg = hfov_deg
    if eff_center_az is None and has_explicit_sector:
        eff_center_az = (az_min + sector_span / 2.0) % 360.0
        eff_hfov_deg = sector_span

    proj_mode = (projection or "auto").lower()
    if proj_mode == "auto":
        if eff_hfov_deg > 100.0 or has_explicit_sector:
            proj_mode = "cylindrical"
        else:
            proj_mode = "rectilinear"

    fov_info = None
    if eff_center_az is not None:
        eff_center_az = eff_center_az % 360.0
        left_edge = (eff_center_az - eff_hfov_deg / 2.0) % 360.0
        right_edge = (eff_center_az + eff_hfov_deg / 2.0) % 360.0
        fov_info = {
            "center_azimuth_deg": round(eff_center_az, 1),
            "center_cardinal": get_compass_cardinal(eff_center_az),
            "hfov_deg": round(eff_hfov_deg, 1),
            "projection": proj_mode,
            "left_edge_deg": round(left_edge, 1),
            "right_edge_deg": round(right_edge, 1),
            "note": "projected_x_fraction: 0.00 = left frame edge, 0.50 = horizontal center, 1.00 = right frame edge",
        }
        if image_width is not None:
            fov_info["image_width_px"] = int(image_width)

    def in_azimuth_sector(bearing):
        if center_az is not None and not has_explicit_sector:
            # Filter to FOV +/- 20 deg margin so nearby off-frame candidates are also shown clearly
            return abs(angular_diff_signed(bearing, center_az)) <= (hfov_deg / 2.0 + 20.0)
        if has_explicit_sector:
            return abs(angular_diff_signed(bearing, eff_center_az)) <= (eff_hfov_deg / 2.0 + 20.0)
        if az_min <= az_max:
            return az_min <= bearing <= az_max
        return bearing >= az_min or bearing <= az_max

    all_named = find_nearby_features(lat, lon, radius_km=radius_km)

    # Parse any explicit --check-coords entries (supports (lat, lon, label) tuples, "Name:lat,lon", or "lat,lon[,Name]")
    extra_check_features = []
    if check_coords:
        for raw_cc in check_coords:
            try:
                if isinstance(raw_cc, (tuple, list)):
                    c_lat = float(raw_cc[0])
                    c_lon = float(raw_cc[1])
                    c_name = str(raw_cc[2]) if len(raw_cc) > 2 else f"Target ({c_lat:.5f}, {c_lon:.5f})"
                else:
                    s_cc = str(raw_cc).strip()
                    if ":" in s_cc:
                        c_name, coords_part = s_cc.rsplit(":", 1)
                        lat_s, lon_s = [p.strip() for p in coords_part.split(",", 1)]
                        c_lat, c_lon = float(lat_s), float(lon_s)
                    else:
                        parts = [p.strip() for p in s_cc.split(",") if p.strip()]
                        c_lat, c_lon = float(parts[0]), float(parts[1])
                        c_name = ",".join(parts[2:]) if len(parts) > 2 else f"Target ({c_lat:.5f}, {c_lon:.5f})"
                calc = calculate_haversine(lat, lon, c_lat, c_lon)
                e_ft, e_m = (dem.sample_ft_m(c_lat, c_lon) if dem is not None else (None, None))
                extra_check_features.append({
                    "name": c_name.strip(),
                    "type": "CheckPoint",
                    "distance_mi": calc["distance_mi"],
                    "distance_km": calc["distance_km"],
                    "bearing_deg": calc["bearing_deg"],
                    "cardinal": calc["cardinal"],
                    "elevation_ft": e_ft,
                    "elevation_m": e_m,
                    "lat": round(c_lat, 6),
                    "lon": round(c_lon, 6),
                    "source": "CLI --check-coords",
                })
            except Exception as exc:
                sys.stderr.write(f"Warning: could not parse --check-coords '{raw_cc}': {exc}\n")

    extended_types = ("Flat", "Bench", "Ridge", "Basin", "Lake", "Valley")
    allowed_types = ("Summit", "Ridge", "Gap", "Saddle", "Cliff", "Pillar", "Lake", "Basin", "Flat", "Bench", "CheckPoint")
    candidate_named = [
        f for f in all_named
        if f.get("type") in allowed_types
        and f.get("type") != "Valley"
        and in_azimuth_sector(f["bearing_deg"])
    ] + extra_check_features

    named_results = []
    visible_features_in_fov = []
    occluded_features_in_fov = []
    checked_coords_visibility = []

    for f in candidate_named:
        prof = compute_elevation_profile(
            lat,
            lon,
            f["lat"],
            f["lon"],
            samples=50,
            target_elev_ft=f.get("elevation_ft"),
            target_elev_m=f.get("elevation_m"),
            dem=dem,
            include_profile=False,
        )
        f_type = f.get("type", "Summit")
        geom_type = "EXTENDED_LANDFORM_CENTROID" if f_type in extended_types else "POINT_SUMMIT"
        f_elev_ft = f.get("elevation_ft") or prof["target_elevation_ft"]
        f_elev_m = f.get("elevation_m") or prof["target_elevation_m"]
        los = prof["line_of_sight"]
        vis_class = prof["visibility_class"]
        vis_note = prof["visibility_note"]

        # Safeguard against false centroid occlusion on extended landforms (Flat/Bench/Ridge/Basin/Lake)
        if geom_type == "EXTENDED_LANDFORM_CENTROID" and los == "OCCLUDED" and prof["occluding_ridge"]:
            occ = prof["occluding_ridge"]
            dist_to_centroid_km = abs(f["distance_km"] - occ["distance_km"])
            elev_diff_m = abs((f_elev_m or 0) - (occ.get("elevation_m") or 0))
            if dist_to_centroid_km <= 1.5 and elev_diff_m <= 180:
                los = "CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"
                vis_class = "CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"
                vis_note = (
                    f"Extended {f_type} interior centroid is foreshortened behind its own near rim/flank at "
                    f"{occ['distance_mi']} mi ({occ['elevation_ft']} ft); outer rim/slopes may still be visible."
                )

        entry = {
            "name": f["name"],
            "type": f_type,
            "geometry_type": geom_type,
            "bearing_deg": f["bearing_deg"],
            "azimuth_deg": f["bearing_deg"],
            "cardinal": f["cardinal"],
            "distance_mi": f["distance_mi"],
            "distance_km": f["distance_km"],
            "elevation_ft": f_elev_ft,
            "elevation_m": f_elev_m,
            "target_vertical_angle_deg": prof["target_vertical_angle_deg"],
            "vertical_angle_deg": prof["target_vertical_angle_deg"],
            "max_intervening_angle_deg": prof["max_intervening_angle_deg"],
            "clearance_deg": prof["clearance_deg"],
            "line_of_sight": los,
            "visibility_class": vis_class,
            "lat": f["lat"],
            "lon": f["lon"],
        }
        if vis_note and vis_class in ("GRAZING_TIP_ONLY", "CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"):
            entry["visibility_note"] = vis_note
        if prof["highest_intervening_ridge"] and los == "VISIBLE":
            entry["highest_intervening_ridge"] = {
                "distance_mi": prof["highest_intervening_ridge"]["distance_mi"],
                "elevation_ft": prof["highest_intervening_ridge"]["elevation_ft"],
                "vertical_angle_deg": prof["highest_intervening_ridge"]["vertical_angle_deg"],
            }
        if prof["occluding_ridge"]:
            entry["occluded_by"] = {
                "distance_mi": prof["occluding_ridge"]["distance_mi"],
                "elevation_ft": prof["occluding_ridge"]["elevation_ft"],
                "vertical_angle_deg": prof["occluding_ridge"]["vertical_angle_deg"],
            }
        if eff_center_az is not None:
            in_fov, delta_deg, x_frac = project_bearing_to_fov(
                f["bearing_deg"], eff_center_az, eff_hfov_deg, projection=proj_mode
            )
            entry["in_fov"] = in_fov
            entry["angle_from_center_deg"] = delta_deg
            entry["projected_x_fraction"] = x_frac
            if image_width is not None and x_frac is not None:
                entry["expected_x_px"] = int(round(x_frac * float(image_width)))
                entry["projected_x_px"] = entry["expected_x_px"]

        if f_type == "CheckPoint":
            checked_coords_visibility.append(entry)
        if los == "VISIBLE":
            visible_features_in_fov.append(entry)
            named_results.append(entry)
        elif f_type != "Valley":
            occ_compact = {
                "name": entry["name"],
                "type": f_type,
                "bearing_deg": entry["bearing_deg"],
                "azimuth_deg": entry["bearing_deg"],
                "distance_mi": entry["distance_mi"],
                "distance_km": entry["distance_km"],
                "elevation_ft": entry["elevation_ft"],
                "elevation_m": entry["elevation_m"],
                "target_vertical_angle_deg": entry["target_vertical_angle_deg"],
                "vertical_angle_deg": entry["target_vertical_angle_deg"],
                "clearance_deg": entry["clearance_deg"],
                "line_of_sight": los,
                "occluded_by": entry.get("occluded_by"),
            }
            if eff_center_az is not None:
                occ_compact["in_fov"] = entry["in_fov"]
                occ_compact["projected_x_fraction"] = entry["projected_x_fraction"]
            if "visibility_note" in entry:
                occ_compact["visibility_note"] = entry["visibility_note"]
            occluded_features_in_fov.append(occ_compact)
            if los == "CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING" or f_type == "CheckPoint":
                named_results.append(entry)

    # Sort named results by bearing when eff_center_az is given, else by bearing_deg
    if eff_center_az is not None:
        sort_fn = lambda x: angular_diff_signed(x["bearing_deg"], eff_center_az)
    else:
        sort_fn = lambda x: x["bearing_deg"]
    named_results.sort(key=sort_fn)
    visible_features_in_fov.sort(key=sort_fn)
    occluded_features_in_fov.sort(key=sort_fn)

    # Compute top 8-10 highest vertical-angle visible summits across all 360 deg fast against in-memory DEM
    top_dominant_360 = []
    if dem is not None:
        evaluated_by_name = {e["name"]: e for e in (visible_features_in_fov + occluded_features_in_fov)}
        all_summits = [
            f for f in all_named
            if f.get("type") in ("Summit", "Pillar", "Cliff") and dem.contains(f["lat"], f["lon"])
        ]
        summit_vis = []
        for sf in all_summits:
            if sf["name"] in evaluated_by_name:
                ev = evaluated_by_name[sf["name"]]
                if ev.get("line_of_sight") == "VISIBLE" and ev.get("target_vertical_angle_deg") is not None:
                    summit_vis.append(ev)
                continue
            prof360 = compute_elevation_profile(
                lat,
                lon,
                sf["lat"],
                sf["lon"],
                samples=40,
                target_elev_ft=sf.get("elevation_ft"),
                target_elev_m=sf.get("elevation_m"),
                dem=dem,
                include_profile=False,
            )
            if prof360["line_of_sight"] == "VISIBLE" and prof360["target_vertical_angle_deg"] is not None:
                summit_vis.append({
                    "name": sf["name"],
                    "type": sf["type"],
                    "bearing_deg": sf["bearing_deg"],
                    "cardinal": sf["cardinal"],
                    "distance_mi": sf["distance_mi"],
                    "distance_km": sf["distance_km"],
                    "elevation_ft": sf.get("elevation_ft") or prof360["target_elevation_ft"],
                    "elevation_m": sf.get("elevation_m") or prof360["target_elevation_m"],
                    "target_vertical_angle_deg": prof360["target_vertical_angle_deg"],
                    "clearance_deg": prof360["clearance_deg"],
                    "visibility_class": prof360["visibility_class"],
                })
        summit_vis.sort(key=lambda x: x["target_vertical_angle_deg"], reverse=True)
        for sv in summit_vis[:10]:
            top_dominant_360.append({
                "name": sv["name"],
                "type": sv["type"],
                "bearing_deg": sv["bearing_deg"],
                "cardinal": sv["cardinal"],
                "distance_mi": sv["distance_mi"],
                "elevation_ft": sv["elevation_ft"],
                "target_vertical_angle_deg": sv["target_vertical_angle_deg"],
                "clearance_deg": sv.get("clearance_deg"),
                "visibility_class": sv.get("visibility_class", "CLEARLY_VISIBLE"),
            })

    # Vectorized DEM horizon skyline rays (500m to radius_km) + separate near-field foreground sweep (50m to 500m)
    horizon_peaks = []
    foreground_ridges = []
    if dem is not None and eye_m is not None:
        ray_azimuths = []
        if center_az is not None and not has_explicit_sector:
            half_span = min(180.0, hfov_deg / 2.0 + 15.0)
            cur = -half_span
            while cur <= half_span + 1e-6:
                ray_azimuths.append((center_az + cur) % 360.0)
                cur += az_step
        else:
            span = (az_max - az_min) if az_max >= az_min else (360.0 - az_min + az_max)
            steps_n = max(1, int(round(span / az_step)))
            for i in range(steps_n + 1):
                ray_azimuths.append((az_min + i * az_step) % 360.0)

        az_arr = np.array(ray_azimuths, dtype=np.float64)
        az_rad = np.radians(az_arr)[:, None]
        cos_lat = math.cos(math.radians(lat))

        # 1. Main horizon highpoints sweep (500.0 m to radius_km * 1000.0)
        dists_m = np.linspace(500.0, radius_km * 1000.0, 160)
        p_lats = lat + (dists_m[None, :] * np.cos(az_rad)) / 111139.0
        p_lons = lon + (dists_m[None, :] * np.sin(az_rad)) / (111139.0 * cos_lat)
        elevs_2d = dem.sample_m_array(p_lats, p_lons)
        drop_2d = (dists_m[None, :] ** 2) / (2.0 * r_eff)
        angs_2d = np.degrees(np.arctan2((elevs_2d - drop_2d) - eye_m, dists_m[None, :]))

        horizon_rays = []
        for idx_az, az in enumerate(ray_azimuths):
            row_angs = angs_2d[idx_az]
            valid_mask = np.isfinite(row_angs)
            if not np.any(valid_mask):
                continue
            safe_angs = np.where(valid_mask, row_angs, -999.0)
            best_j = int(np.argmax(safe_angs))
            best_angle = float(safe_angs[best_j])
            d_m = float(dists_m[best_j])
            e_m = float(elevs_2d[idx_az, best_j])
            p_lat = float(p_lats[idx_az, best_j])
            p_lon = float(p_lons[idx_az, best_j])
            e_ft = round(e_m * 3.280839895)
            horizon_rays.append({
                "azimuth_deg": round(az, 1),
                "bearing_deg": round(az, 1),
                "cardinal": get_compass_cardinal(az),
                "vertical_angle_deg": round(best_angle, 2),
                "target_vertical_angle_deg": round(best_angle, 2),
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
                matched_name = None
                nearest_nf_str = None
                best_nf_dist = 2.51
                for nf in all_named:
                    nf_type = nf.get("type")
                    if nf_type in ("Summit", "Ridge", "Gap", "Saddle", "Peak", "Cliff", "Pillar"):
                        d_match = calculate_haversine(r["lat"], r["lon"], nf["lat"], nf["lon"])
                        d_km = d_match["distance_km"]
                        if d_km <= 0.45 and matched_name is None and nf_type in ("Summit", "Ridge", "Peak", "Cliff", "Pillar"):
                            matched_name = nf["name"]
                        if d_km <= 2.5 and d_km < best_nf_dist:
                            best_nf_dist = d_km
                            nearest_nf_str = f"{nf['name']} ({nf_type}, {d_km:.2f} km / {d_match['distance_mi']:.2f} mi)"
                item = dict(r)
                matched_label = matched_name or f"Unnamed Horizon Highpoint (~{r['elevation_ft']:,} ft / {r['elevation_m']:,} m)"
                item["matched_named_feature"] = matched_label
                item["matched_feature"] = matched_label
                item["nearest_named_feature"] = nearest_nf_str
                if eff_center_az is not None:
                    in_fov, delta_deg, x_frac = project_bearing_to_fov(
                        r["azimuth_deg"], eff_center_az, eff_hfov_deg, projection=proj_mode
                    )
                    item["in_fov"] = in_fov
                    item["angle_from_center_deg"] = delta_deg
                    item["projected_x_fraction"] = x_frac
                    if image_width is not None and x_frac is not None:
                        item["expected_x_px"] = int(round(x_frac * float(image_width)))
                        item["projected_x_px"] = item["expected_x_px"]
                if horizon_peaks and abs(angular_diff_signed(item["azimuth_deg"], horizon_peaks[-1]["azimuth_deg"])) <= 2.5:
                    if item["vertical_angle_deg"] > horizon_peaks[-1]["vertical_angle_deg"]:
                        horizon_peaks[-1] = item
                else:
                    horizon_peaks.append(item)

        # 2. Separate near-field foreground sweep (50.0 m to 500.0 m)
        fg_dists_m = np.linspace(50.0, 500.0, 20)
        fg_lats = lat + (fg_dists_m[None, :] * np.cos(az_rad)) / 111139.0
        fg_lons = lon + (fg_dists_m[None, :] * np.sin(az_rad)) / (111139.0 * cos_lat)
        fg_elevs = dem.sample_m_array(fg_lats, fg_lons)
        fg_drop = (fg_dists_m[None, :] ** 2) / (2.0 * r_eff)
        fg_angs = np.degrees(np.arctan2((fg_elevs - fg_drop) - eye_m, fg_dists_m[None, :]))

        fg_rays = []
        for idx_az, az in enumerate(ray_azimuths):
            row_angs = fg_angs[idx_az]
            valid_mask = np.isfinite(row_angs)
            if not np.any(valid_mask):
                continue
            safe_angs = np.where(valid_mask, row_angs, -999.0)
            best_j = int(np.argmax(safe_angs))
            best_angle = float(safe_angs[best_j])
            if best_angle <= 0.0:
                continue
            d_m = float(fg_dists_m[best_j])
            e_m = float(fg_elevs[idx_az, best_j])
            p_lat = float(fg_lats[idx_az, best_j])
            p_lon = float(fg_lons[idx_az, best_j])
            e_ft_fg = round(e_m * 3.280839895)
            e_m_fg = round(e_m)
            fg_lbl = f"Foreground Ridge (~{e_ft_fg:,} ft / {e_m_fg:,} m)"
            fg_rays.append({
                "azimuth_deg": round(az, 1),
                "bearing_deg": round(az, 1),
                "cardinal": get_compass_cardinal(az),
                "vertical_angle_deg": round(best_angle, 2),
                "target_vertical_angle_deg": round(best_angle, 2),
                "distance_m": round(d_m, 1),
                "distance_km": round(d_m / 1000.0, 2),
                "distance_mi": round(d_m / 1609.344, 2),
                "elevation_ft": e_ft_fg,
                "elevation_m": e_m_fg,
                "lat": round(p_lat, 5),
                "lon": round(p_lon, 5),
                "matched_named_feature": fg_lbl,
                "matched_feature": fg_lbl,
            })

        for i, r in enumerate(fg_rays):
            left_ang = fg_rays[i - 1]["vertical_angle_deg"] if i > 0 else r["vertical_angle_deg"] - 0.1
            right_ang = fg_rays[i + 1]["vertical_angle_deg"] if i + 1 < len(fg_rays) else r["vertical_angle_deg"] - 0.1
            if r["vertical_angle_deg"] >= left_ang and r["vertical_angle_deg"] >= right_ang:
                item = dict(r)
                if eff_center_az is not None:
                    in_fov, delta_deg, x_frac = project_bearing_to_fov(
                        r["azimuth_deg"], eff_center_az, eff_hfov_deg, projection=proj_mode
                    )
                    item["in_fov"] = in_fov
                    item["angle_from_center_deg"] = delta_deg
                    item["projected_x_fraction"] = x_frac
                    if image_width is not None and x_frac is not None:
                        item["expected_x_px"] = int(round(x_frac * float(image_width)))
                        item["projected_x_px"] = item["expected_x_px"]
                if foreground_ridges and abs(angular_diff_signed(item["azimuth_deg"], foreground_ridges[-1]["azimuth_deg"])) <= 8.0:
                    if item["vertical_angle_deg"] > foreground_ridges[-1]["vertical_angle_deg"]:
                        foreground_ridges[-1] = item
                else:
                    foreground_ridges.append(item)

    out = {
        "camera": {
            "lat": lat,
            "lon": lon,
            "elevation_ft": cam_ft,
            "elevation_m": cam_m,
        },
        "fov": fov_info,
    }
    if checked_coords_visibility:
        out["checked_coords_visibility"] = checked_coords_visibility
    out["visible_features_in_fov"] = visible_features_in_fov
    out["dem_horizon_highpoints"] = horizon_peaks
    out["foreground_ridges"] = foreground_ridges
    out["top_dominant_360_visible_summits"] = top_dominant_360
    out["occluded_features_in_fov"] = occluded_features_in_fov
    out["named_features_visibility"] = named_results
    return out


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
    raw_png = _fetch_url_bytes(url, timeout=15.0)

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
    include_skyline_lists=True,
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
    elevs = dem.sample_m_array(p_lats, p_lons)

    # Smoothed radial slope over ~150m window along each ray to detect flat basins/meadows/lakes cleanly
    step_m = float(dists[1] - dists[0])
    pad_e = np.pad(elevs, ((0, 0), (2, 2)), mode="edge")
    radial_grad = np.abs(pad_e[:, 4:] - pad_e[:, :-4]) / (4.0 * step_m)

    # Earth curvature & refraction correction
    r_eff = 6371000.0 / (1.0 - 0.13)
    drop = (dists[None, :] ** 2) / (2.0 * r_eff)
    apparent_h = elevs - drop
    angs = np.degrees(np.arctan2(apparent_h - eye_m, dists[None, :]))

    # Auto-adjust alt_max: when center_az is provided, compute vertical auto-scaling over columns within
    # the camera's horizontal FOV (ignoring <1.5km foreground side-walls in the outer 15% frame edges
    # unless the entire frame is <1.5km canyon walls).
    if center_az is not None:
        col_diffs = np.abs(((az_vals - float(center_az) + 180.0) % 360.0) - 180.0)
        in_fov_cols = col_diffs <= (hfov_deg / 2.0)
        in_core_cols = col_diffs <= (hfov_deg / 2.0) * 0.70
        dist_mask = dists[None, :] >= 1500.0
        scale_mask = (in_core_cols[:, None]) | (in_fov_cols[:, None] & dist_mask)
        cand_angs = angs[scale_mask & np.isfinite(angs)]
        if cand_angs.size == 0 or float(np.max(cand_angs)) < 0.0:
            cand_angs = angs[in_fov_cols[:, None] & np.isfinite(angs)]
        finite_angs = cand_angs if cand_angs.size > 0 else angs[np.isfinite(angs)]
    else:
        finite_angs = angs[np.isfinite(angs)]

    if finite_angs.size > 0:
        max_terrain_ang = float(np.max(finite_angs))
        if max_terrain_ang + 3.0 > alt_max:
            alt_max = min(35.0, float(math.ceil((max_terrain_ang + 4.0) / 3.0) * 3.0))

    pano_h = 460
    pano_w = n_cols
    pano_img = np.zeros((pano_h, pano_w, 3), dtype=np.uint8)
    pano_img[:, :] = [205, 222, 255]  # sky background

    # Front-to-back vertical column painter with subdivided foreground bands and sensitive ridge contours
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
                    elif d < 500.0:
                        color = [40, 75, 45]     # immediate foreground (<0.5km) deep pine green
                    elif d < 1000.0:
                        color = [55, 95, 58]     # near foreground (0.5-1.0km) forest green
                    elif d < 2000.0:
                        color = [72, 110, 78]    # mid-near foreground (1.0-2.0km) sage-forest green
                    elif d < 6000.0:
                        color = [90, 120, 110]   # mid-ground green-slate ridge
                    elif d < 12000.0:
                        color = [130, 140, 170]  # distant slate-blue ridge
                    else:
                        color = [160, 170, 198]  # far horizon ridge
                    pano_img[r0:r1, col] = color
                    # Draw ridge-crest contour line when distance jumps across an occluded valley or spur
                    if prev_d > 0.0 and (d - prev_d) > max(180.0, 0.22 * prev_d) and r1 < pano_h:
                        pano_img[max(0, r1 - 1):r1, col] = [40, 50, 60]
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
            tip_tag = ""
            if nf.get("visibility_class") == "GRAZING_TIP_ONLY":
                clr_val = nf.get("clearance_deg")
                tip_tag = f" [TIP ONLY +{clr_val:.2f} deg]" if clr_val is not None else " [TIP ONLY]"
            callouts.append({
                "az": nf["bearing_deg"],
                "alt": nf["target_vertical_angle_deg"],
                "label": f"{nf['name']} ({nf['elevation_ft']}ft, {nf['bearing_deg']:.1f}deg){tip_tag}",
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
        "Colors: Deep/Forest/Sage Green (<0.5/1/2km) | Slate-Green (2-6km) | Slate-Blue (6-12km) | Pale Blue (>12km) | Gold (Flat Basin)",
        fill=(70, 70, 70),
    )
    draw.text((pad_l + pano_w // 2 - 55, pad_t + pano_h + 36), "Azimuth (degrees)", fill=(0, 0, 0))
    draw.text((6, pad_t - 18), "Alt (deg)", fill=(0, 0, 0))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    canvas.save(out_path)
    out = {
        "output_path": os.path.abspath(out_path),
        "az_start_deg": round(az_start % 360.0, 1),
        "az_span_deg": round(az_span, 1),
        "alt_min_deg": round(alt_min, 1),
        "alt_max_deg": round(alt_max, 1),
        "annotated_features_count": len(callouts),
    }
    if include_skyline_lists and skyline_data:
        out["visible_features_in_fov"] = skyline_data.get("visible_features_in_fov", [])
        out["foreground_ridges"] = skyline_data.get("foreground_ridges", [])
        out["dem_horizon_highpoints"] = skyline_data.get("dem_horizon_highpoints", [])
    return out


def format_compact_json(data, indent_level=0):
    """
    Formats a Python dict/list as 100% valid JSON while keeping list-of-dict arrays
    to 1 compact single-line JSON object per element so stdout stays < 8 KB.
    """
    pad = "  " * indent_level
    next_pad = "  " * (indent_level + 1)
    if isinstance(data, dict):
        if not data:
            return "{}"
        lines = ["{"]
        items = list(data.items())
        for idx, (k, v) in enumerate(items):
            comma = "," if idx < len(items) - 1 else ""
            k_str = json.dumps(str(k), ensure_ascii=False)
            if isinstance(v, (dict, list)):
                v_str = format_compact_json(v, indent_level + 1)
                lines.append(f"{next_pad}{k_str}: {v_str}{comma}")
            else:
                v_str = json.dumps(v, ensure_ascii=False)
                lines.append(f"{next_pad}{k_str}: {v_str}{comma}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    elif isinstance(data, list):
        if not data:
            return "[]"
        if all(isinstance(el, dict) for el in data):
            lines = ["["]
            for idx, el in enumerate(data):
                comma = "," if idx < len(data) - 1 else ""
                el_str = json.dumps(el, ensure_ascii=False, separators=(", ", ": "))
                lines.append(f"{next_pad}{el_str}{comma}")
            lines.append(f"{pad}]")
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False)
    else:
        return json.dumps(data, ensure_ascii=False)


def _emit_json(data, out_json_path=None, stdout_data=None):
    formatted_full = format_compact_json(data)
    if out_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_json_path)), exist_ok=True)
        with open(out_json_path, "w", encoding="utf-8") as f:
            f.write(formatted_full + "\n")
    if stdout_data is not None:
        print(format_compact_json(stdout_data), flush=True)
    else:
        print(formatted_full, flush=True)


def _filter_feature_list(
    features,
    types=None,
    az_min=None,
    az_max=None,
    max_dist_mi=None,
    max_dist_km=None,
    search=None,
    max_results=None,
):
    if not features:
        return []
    allowed_types = None
    if types:
        raw_types = [t.strip().lower() for t in str(types).split(",") if t.strip()]
        expanded = set(raw_types)
        if "peak" in expanded or "summit" in expanded:
            expanded.update(("peak", "summit"))
        if "pass" in expanded or "col" in expanded or "gap" in expanded or "saddle" in expanded:
            expanded.update(("pass", "col", "gap", "saddle"))
        if "tarn" in expanded or "water" in expanded or "lake" in expanded or "pond" in expanded:
            expanded.update(("tarn", "water", "lake", "pond"))
        allowed_types = expanded

    search_terms = None
    if search:
        search_terms = [s.strip().lower() for s in str(search).split(",") if s.strip()]

    out = []
    for f in features:
        if allowed_types is not None:
            f_type = str(f.get("type") or "").strip().lower()
            if f_type not in allowed_types:
                continue
        b = f.get("bearing_deg")
        if b is None:
            b = f.get("azimuth_deg")
        if b is not None and (az_min is not None or az_max is not None):
            b_val = float(b) % 360.0
            if az_min is not None and az_max is not None:
                a_min = float(az_min) % 360.0
                a_max = float(az_max) % 360.0
                if a_min <= a_max:
                    if not (a_min <= b_val <= a_max):
                        continue
                else:
                    if not (b_val >= a_min or b_val <= a_max):
                        continue
            elif az_min is not None:
                if b_val < float(az_min):
                    continue
            elif az_max is not None:
                if b_val > float(az_max):
                    continue
        if max_dist_mi is not None:
            d_mi = f.get("distance_mi")
            if d_mi is not None and float(d_mi) > float(max_dist_mi):
                continue
        if max_dist_km is not None:
            d_km = f.get("distance_km")
            if d_km is None and f.get("distance_mi") is not None:
                d_km = float(f["distance_mi"]) * 1.609344
            if d_km is not None and float(d_km) > float(max_dist_km):
                continue
        if search_terms:
            haystack = " ".join(
                str(f.get(k) or "")
                for k in ("name", "matched_named_feature", "nearest_named_feature", "type")
            ).lower()
            if not any(term in haystack for term in search_terms):
                continue
        out.append(f)

    if max_results is not None and int(max_results) > 0:
        out = out[: int(max_results)]
    return out


def _reorder_skyline_dict(res):
    ordered_keys = (
        "camera",
        "fov",
        "checked_coords_visibility",
        "visible_features_in_fov",
        "dem_horizon_highpoints",
        "foreground_ridges",
        "top_dominant_360_visible_summits",
        "synthetic_pano",
        "occluded_features_in_fov",
        "named_features_visibility",
    )
    out = {}
    for k in ordered_keys:
        if k in res:
            out[k] = res[k]
    for k, v in res.items():
        if k not in out:
            out[k] = v
    return out


def _build_skyline_stdout(res, out_json_path=None, verbose=False):
    if not out_json_path or verbose:
        return res

    fov_info = res.get("fov")
    center_az = fov_info.get("center_azimuth_deg") if isinstance(fov_info, dict) else None
    hfov_deg = fov_info.get("hfov_deg", 54.4) if isinstance(fov_info, dict) else 54.4

    all_occ = res.get("occluded_features_in_fov", [])
    if center_az is not None:
        fov_plus_10 = [
            o for o in all_occ
            if abs(angular_diff_signed(o.get("bearing_deg", 0.0), center_az)) <= (hfov_deg / 2.0 + 10.0)
            or o.get("line_of_sight") == "CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"
        ]
    else:
        fov_plus_10 = list(all_occ)

    # Pick up to 12 closest / highest occluded features within FOV +/- 10 deg
    centroid_occ = [o for o in fov_plus_10 if o.get("line_of_sight") == "CENTROID_OCCLUDED_BY_RIM_OR_FORESHORTENING"]
    by_closest = sorted(fov_plus_10, key=lambda o: o.get("distance_mi") if o.get("distance_mi") is not None else 999.0)
    by_highest = sorted(fov_plus_10, key=lambda o: o.get("elevation_ft") or 0, reverse=True)

    picked_occ = []
    seen_occ = set()
    for cand in centroid_occ + by_closest[:6] + by_highest[:6] + by_closest:
        if len(picked_occ) >= 12:
            break
        key = (cand.get("name"), cand.get("bearing_deg"))
        if key not in seen_occ:
            seen_occ.add(key)
            picked_occ.append(cand)

    if center_az is not None:
        picked_occ.sort(key=lambda x: angular_diff_signed(x.get("bearing_deg", 0.0), center_az))
    else:
        picked_occ.sort(key=lambda x: x.get("bearing_deg", 0.0))

    def _compact_dict(item, level):
        if level <= 0:
            return item
        if level == 1:
            drop = {
                "geometry_type", "expected_x_px", "projected_x_px", "source",
                "lat", "lon", "horizon_distance_mi", "horizon_distance_km",
                "horizon_elevation_ft", "horizon_elevation_m", "occluding_lat", "occluding_lon",
            }
            return {k: v for k, v in item.items() if k not in drop}
        # level == 2: compact stdout representation (full fields + aliases remain in --out-json)
        if level == 2:
            keep_order = (
                "name", "type", "bearing_deg", "azimuth_deg", "cardinal",
                "distance_mi", "elevation_ft", "target_vertical_angle_deg",
                "in_fov", "projected_x_fraction", "line_of_sight", "occluding_feature",
                "matched_named_feature", "nearest_named_feature",
            )
            out_d = {}
            for k in keep_order:
                if k in item and item[k] is not None:
                    if k == "azimuth_deg" and "bearing_deg" in out_d:
                        continue
                    if k == "line_of_sight" and item[k] == "VISIBLE":
                        continue
                    out_d[k] = item[k]
            return out_d
        # level >= 3: tightest stdout representation to guarantee < 4.5 KB
        keep_order_tight = (
            "name", "type", "bearing_deg", "azimuth_deg",
            "distance_mi", "elevation_ft", "target_vertical_angle_deg",
            "in_fov", "projected_x_fraction", "occluding_feature",
            "matched_named_feature", "nearest_named_feature",
        )
        out_d = {}
        for k in keep_order_tight:
            if k in item and item[k] is not None:
                if k == "azimuth_deg" and "bearing_deg" in out_d:
                    continue
                out_d[k] = item[k]
        return out_d

    def _assemble(occ_slice, top360_slice, level=0, max_vis=None, max_hp=None, max_fg=None):
        vis_raw = res.get("visible_features_in_fov", [])
        hp_raw = res.get("dem_horizon_highpoints", [])
        fg_raw = res.get("foreground_ridges", [])
        if max_vis is not None and len(vis_raw) > max_vis:
            in_fov_vis = [x for x in vis_raw if x.get("in_fov")]
            out_fov_vis = [x for x in vis_raw if not x.get("in_fov")]
            vis_raw = (in_fov_vis + out_fov_vis)[:max_vis]
        if max_hp is not None and len(hp_raw) > max_hp:
            in_fov_hp = [x for x in hp_raw if x.get("in_fov")]
            out_fov_hp = [x for x in hp_raw if not x.get("in_fov")]
            hp_raw = (in_fov_hp + out_fov_hp)[:max_hp]
        if max_fg is not None and len(fg_raw) > max_fg:
            fg_raw = fg_raw[:max_fg]

        vis_list = [_compact_dict(item, level) for item in vis_raw]
        hp_list = [_compact_dict(item, level) for item in hp_raw]
        fg_list = [_compact_dict(item, level) for item in fg_raw]
        occ_list = [_compact_dict(item, level) for item in occ_slice]
        top360_list = [_compact_dict(item, level) for item in top360_slice]

        out_stdout = {
            "camera": res.get("camera"),
            "fov": res.get("fov"),
        }
        if "checked_coords_visibility" in res:
            out_stdout["checked_coords_visibility"] = res["checked_coords_visibility"]
        out_stdout["visible_features_in_fov"] = vis_list
        out_stdout["dem_horizon_highpoints"] = hp_list
        out_stdout["foreground_ridges"] = fg_list
        out_stdout["top_dominant_360_visible_summits"] = top360_list
        if "synthetic_pano" in res:
            out_stdout["synthetic_pano"] = res["synthetic_pano"]
        out_stdout["occluded_in_fov_total_count"] = len(all_occ)
        out_stdout["occluded_features_in_fov"] = occ_list
        return out_stdout

    top360_full = res.get("top_dominant_360_visible_summits", [])
    for occ_n, top_n, lvl, mv, mhp, mfg in (
        (12, len(top360_full), 0, None, None, None),
        (12, 8, 1, None, None, None),
        (12, 6, 2, None, None, None),
        (8, 5, 2, None, None, None),
        (6, 5, 3, None, None, None),
        (4, 4, 3, None, None, None),
        (4, 4, 3, 12, 10, 5),
        (3, 3, 3, 9, 7, 4),
        (2, 2, 3, 7, 5, 3),
        (1, 2, 3, 6, 4, 2),
    ):
        cand_stdout = _assemble(
            picked_occ[:occ_n],
            top360_full[:top_n],
            level=lvl,
            max_vis=mv,
            max_hp=mhp,
            max_fg=mfg,
        )
        if len(format_compact_json(cand_stdout)) <= 4450:
            return cand_stdout
    return cand_stdout


def _build_nearby_stdout(full_features, filtered_features, out_json_path=None, verbose=False, has_explicit_filters=False):
    if not out_json_path or verbose:
        return filtered_features

    major_types = {
        "Summit", "Lake", "Gap", "Saddle", "Ridge", "Basin", "Valley",
        "Cliff", "Pillar", "Falls", "Glacier", "Range", "Arch", "Crater",
    }
    if has_explicit_filters:
        retained = list(filtered_features)
    else:
        retained = [
            f for f in filtered_features
            if float(f.get("distance_km", 999.0)) <= 5.0 or f.get("type") in major_types
        ]

    def _make_summary(feat_slice, strip_source=False, round_coords=False, compact_keys=False):
        items = []
        for item in feat_slice:
            if compact_keys:
                keep = ("name", "type", "distance_mi", "distance_km", "bearing_deg", "cardinal", "elevation_ft", "elevation_m")
                d = {k: item[k] for k in keep if k in item and item[k] is not None}
            else:
                d = {k: v for k, v in item.items() if (not strip_source or k != "source")}
                if round_coords:
                    if d.get("lat") is not None:
                        d["lat"] = round(float(d["lat"]), 4)
                    if d.get("lon") is not None:
                        d["lon"] = round(float(d["lon"]), 4)
            items.append(d)
        return {
            "out_json": os.path.abspath(out_json_path),
            "total_features_written_to_file": len(full_features),
            "stdout_features_shown": len(items),
            "features": items,
        }

    summary = _make_summary(retained, strip_source=False)
    if len(format_compact_json(summary)) <= 5450:
        return summary

    # Strip redundant "source" key and round coords on stdout copy
    summary = _make_summary(retained, strip_source=True, round_coords=True)
    if len(format_compact_json(summary)) <= 5450:
        return summary

    # Prioritize closest features (<= 3.0 km), unnamed tarns (<= 1.5 km), and diverse major landforms across the basin
    within_3km = [f for f in retained if float(f.get("distance_km", 999.0)) <= 3.0]
    beyond_3km = [f for f in retained if float(f.get("distance_km", 999.0)) > 3.0]
    priority_types = {"Gap", "Saddle", "Ridge", "Basin", "Valley", "Cliff", "Pillar"}
    beyond_3km.sort(
        key=lambda f: (
            0 if f.get("type") in priority_types else (
                1 if (f.get("type") == "Summit" and (f.get("elevation_ft") or 0) >= 12500) else (
                    2 if f.get("type") == "Lake" and not f.get("is_unnamed_water") else 3
                )
            ),
            float(f.get("distance_km", 99.0)),
            -(f.get("elevation_ft") or 0),
        )
    )
    prioritized = within_3km + beyond_3km

    for compact_flag in (False, True):
        for cap in (40, 34, 30, 26, 22, 18):
            sliced = sorted(prioritized[:cap], key=lambda x: x.get("distance_km", 0.0))
            summary = _make_summary(sliced, strip_source=True, round_coords=True, compact_keys=compact_flag)
            if len(format_compact_json(summary)) <= 5450:
                return summary
    return summary


def main():
    parser = argparse.ArgumentParser(description="Geographic, 3DEP DEM skyline, and solar analysis utility for hiking photos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: distance
    dist_parser = subparsers.add_parser("distance", help="Compute distance and bearing between two points.")
    dist_parser.add_argument("lat1", type=float, help="Latitude of point 1")
    dist_parser.add_argument("lon1", type=float, help="Longitude of point 1")
    dist_parser.add_argument("lat2", type=float, help="Latitude of point 2")
    dist_parser.add_argument("lon2", type=float, help="Longitude of point 2")
    dist_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    # Subcommand: sun-position (and alias 'sun')
    for cmd_name in ("sun-position", "sun"):
        sun_parser = subparsers.add_parser(cmd_name, help="Compute solar elevation, solar azimuth, and shadow direction.")
        sun_parser.add_argument("lat", type=float, help="Latitude")
        sun_parser.add_argument("lon", type=float, help="Longitude")
        sun_parser.add_argument("datetime_str", help="Date and time string (YYYY-MM-DD HH:MM:SS or ISO format)")
        sun_parser.add_argument("--tz-offset", type=float, default=None, help="UTC offset hours (e.g. -7 for PDT, -8 for PST)")
        sun_parser.add_argument("--center-az", type=float, default=None, help="Optional camera center azimuth in degrees for expected 2D image-plane shadow angle")
        sun_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    # Subcommand: calibrate-azimuth
    cal_parser = subparsers.add_parser(
        "calibrate-azimuth",
        help="Solve for exact camera center_azimuth_deg, left_edge_deg, and right_edge_deg from 1+ anchor bearings and x_fractions, or project x_frac/bearings from --center-az.",
    )
    cal_parser.add_argument("--hfov-deg", "--fov-deg", "--fov", dest="hfov_deg", type=float, required=True, help="Horizontal FOV in degrees")
    cal_parser.add_argument(
        "--anchor",
        action="append",
        required=False,
        default=None,
        help="Anchor specification 'bearing_deg:x_fraction', 'Name:bearing_deg:x_fraction', or 'bearing_deg,x_fraction' (e.g. --anchor 290.1:0.75); may be passed multiple times",
    )
    cal_parser.add_argument(
        "--center-az",
        "--azimuth",
        dest="center_az",
        type=float,
        default=None,
        help="Camera center azimuth in degrees (when projecting --x-frac or --project-bearings without --anchor)",
    )
    cal_parser.add_argument(
        "--x-frac",
        "--x-fraction",
        dest="x_frac",
        action="append",
        default=None,
        help="Comma-separated x_fraction values (0.0=left, 0.5=center, 1.0=right) to forward-project to compass bearings (e.g. --x-frac 0.10,0.57,0.70)",
    )
    cal_parser.add_argument(
        "--project-bearings",
        "--bearings",
        dest="project_bearings",
        action="append",
        default=None,
        help="Comma-separated bearings or 'Name:bearing' pairs to project into x_fraction space (e.g. --project-bearings 73.2,109.0 or 'PeakA:73.2,PeakB:109.0')",
    )
    cal_parser.add_argument(
        "--projection",
        choices=["auto", "rectilinear", "cylindrical"],
        default="auto",
        help="Projection model (default: auto -> cylindrical when hfov > 100 deg else rectilinear)",
    )
    cal_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    # Subcommand: admin-boundaries
    adm_parser = subparsers.add_parser(
        "admin-boundaries",
        help="Query exact US State, County, National Park, Wilderness, and National Forest for GPS coordinates.",
    )
    adm_parser.add_argument("lat", type=float, help="Latitude")
    adm_parser.add_argument("lon", type=float, help="Longitude")
    adm_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    # Subcommand: nearby-features
    peaks_parser = subparsers.add_parser("nearby-features", help="Query named summits, ridges, lakes, and basins around coordinates.")
    peaks_parser.add_argument("lat", type=float, help="Latitude")
    peaks_parser.add_argument("lon", type=float, help="Longitude")
    peaks_parser.add_argument("--radius-km", type=float, default=12.0, help="Search radius in kilometers (default: 12.0)")
    peaks_parser.add_argument("--types", default=None, help="Case-insensitive comma-separated feature class filter (e.g. Lake,Summit,Gap,Ridge,Basin)")
    peaks_parser.add_argument("--az-min", type=float, default=None, help="Minimum bearing filter in degrees (handles 360 wrap-around when > --az-max)")
    peaks_parser.add_argument("--az-max", type=float, default=None, help="Maximum bearing filter in degrees")
    peaks_parser.add_argument("--max-dist-mi", type=float, default=None, help="Maximum distance filter in miles")
    peaks_parser.add_argument("--max-dist-km", type=float, default=None, help="Maximum distance filter in kilometers")
    peaks_parser.add_argument("--search", default=None, help="Case-insensitive comma-separated substring filter on feature names")
    peaks_parser.add_argument("--max-results", type=int, default=None, help="Maximum number of results to print")
    peaks_parser.add_argument("--verbose", action="store_true", help="Include distant (>5km) minor stream/flat/bench features and disable stdout compaction")
    peaks_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write full unfiltered JSON output")

    # Subcommand: elevation-profile
    prof_parser = subparsers.add_parser(
        "elevation-profile",
        help="Check line-of-sight occlusion and elevation profile between camera and target (by lat2/lon2 or --bearing-deg + --dist-km/--dist-mi).",
    )
    prof_parser.add_argument("lat1", type=float, help="Camera latitude")
    prof_parser.add_argument("lon1", type=float, help="Camera longitude")
    prof_parser.add_argument("lat2", nargs="?", type=float, default=None, help="Target latitude (optional if --bearing-deg and --dist-km/--dist-mi are supplied)")
    prof_parser.add_argument("lon2", nargs="?", type=float, default=None, help="Target longitude (optional if --bearing-deg and --dist-km/--dist-mi are supplied)")
    prof_parser.add_argument("--bearing-deg", "--bearing", dest="bearing_deg", type=float, default=None, help="Forward ray-tracing compass bearing in degrees")
    prof_parser.add_argument("--dist-km", type=float, default=None, help="Forward ray-tracing distance in kilometers")
    prof_parser.add_argument("--dist-mi", type=float, default=None, help="Forward ray-tracing distance in miles")
    prof_parser.add_argument("--samples", "--steps", dest="samples", type=int, default=40, help="Number of sample intervals along ray (default: 40)")
    prof_parser.add_argument("--full-profile", action="store_true", help="Include full step-by-step elevation array in JSON output")
    prof_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    # Subcommand: horizon-skyline
    sky_parser = subparsers.add_parser(
        "horizon-skyline",
        help="Compute 3DEP DEM topographic horizon (including unnamed peaks), named summit visibility, and FOV pixel-X projections.",
    )
    sky_parser.add_argument("lat", type=float, help="Camera latitude")
    sky_parser.add_argument("lon", type=float, help="Camera longitude")
    sky_parser.add_argument("--radius-km", type=float, default=22.0, help="Search radius in kilometers (default: 22.0)")
    sky_parser.add_argument("--az-min", type=float, default=0.0, help="Minimum azimuth in degrees (default: 0.0)")
    sky_parser.add_argument("--az-max", type=float, default=360.0, help="Maximum azimuth in degrees (default: 360.0)")
    sky_parser.add_argument("--az-step", type=float, default=1.0, help="Azimuth step in degrees for DEM ray sweep (default: 1.0)")
    sky_parser.add_argument("--center-az", type=float, default=None, help="Estimated camera center azimuth (degrees) to compute FOV bounds and pixel-X fraction")
    sky_parser.add_argument("--hfov-deg", "--fov-deg", "--fov", dest="hfov_deg", type=float, default=54.4, help="Horizontal field of view in degrees (default: 54.4 for 35mm full-frame)")
    sky_parser.add_argument(
        "--projection",
        choices=["auto", "rectilinear", "cylindrical"],
        default="auto",
        help="FOV projection model: auto (cylindrical when hfov > 100 deg else rectilinear), rectilinear, or cylindrical",
    )
    sky_parser.add_argument(
        "--check-coords",
        action="append",
        default=None,
        help="Optional 'lat,lon[,label]' target to evaluate in the same DEM pass (may be passed multiple times)",
    )
    sky_parser.add_argument("--types", default=None, help="Case-insensitive comma-separated feature class filter (e.g. Lake,Summit,Gap,Ridge,Basin)")
    sky_parser.add_argument("--max-dist-mi", type=float, default=None, help="Maximum feature distance filter in miles")
    sky_parser.add_argument("--max-dist-km", type=float, default=None, help="Maximum feature distance filter in kilometers")
    sky_parser.add_argument("--search", default=None, help="Case-insensitive comma-separated substring filter on feature names")
    sky_parser.add_argument("--verbose", action="store_true", help="Disable stdout compaction when --out-json is used")
    sky_parser.add_argument("--image-width", type=int, default=None, help="Optional image width in pixels to include integer expected_x_px")
    sky_parser.add_argument("--out-pano", default=None, help="Optional PNG path to also render an annotated 2D synthetic DEM panorama (e.g. scratch/281A5933_pano.png)")
    sky_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

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
    pano_parser.add_argument("--center-az", "--azimuth", dest="center_az", type=float, default=None, help="Optional camera center azimuth in degrees to draw FOV bounds")
    pano_parser.add_argument("--hfov-deg", "--fov-deg", "--fov", dest="hfov_deg", type=float, default=54.4, help="Horizontal field of view in degrees (default: 54.4)")
    pano_parser.add_argument("--alt-min", type=float, default=-15.0, help="Minimum altitude angle in degrees (default: -15.0)")
    pano_parser.add_argument("--alt-max", type=float, default=15.0, help="Maximum altitude angle in degrees (default: 15.0, auto-expands for steep walls)")
    pano_parser.add_argument("--verbose", action="store_true", help="Include full skyline arrays in stdout even when --out-json is passed")
    pano_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    # Subcommand: render-topo
    topo_parser = subparsers.add_parser(
        "render-topo",
        help="Download and annotate a USGS Topo map centered on camera with optional FOV wedge, bearing rays, and nearby summits.",
    )
    topo_parser.add_argument("lat", type=float, help="Camera latitude")
    topo_parser.add_argument("lon", type=float, help="Camera longitude")
    topo_parser.add_argument("out_pos", nargs="?", default=None, help="Optional positional output PNG file path")
    topo_parser.add_argument("--out", dest="out_opt", default=None, help="Output PNG file path (e.g. scratch/281A5985_topo.png)")
    topo_parser.add_argument("--radius-km", type=float, default=5.0, help="Map half-width radius in kilometers (default: 5.0)")
    topo_parser.add_argument("--center-az", "--azimuth", dest="center_az", type=float, default=None, help="Optional camera center azimuth to draw FOV wedge")
    topo_parser.add_argument("--hfov-deg", "--fov-deg", "--fov", dest="hfov_deg", type=float, default=54.4, help="Horizontal FOV in degrees when drawing FOV wedge (default: 54.4)")
    topo_parser.add_argument("--rays", default=None, help="Optional comma-separated bearing rays in degrees (e.g. '90,180,270')")
    topo_parser.add_argument("--zoom", type=int, default=None, help="Optional compatibility flag (bounding-box scale is controlled by --radius-km)")
    topo_parser.add_argument("--out-json", "--json-out", dest="out_json", default=None, help="Optional file path to write JSON output")

    args = parser.parse_args()

    if args.command == "distance":
        res = calculate_haversine(args.lat1, args.lon1, args.lat2, args.lon2)
        _emit_json(res, args.out_json)

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

        res = calculate_solar_position(args.lat, args.lon, dt, tz_override, center_az=args.center_az)
        res["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        res["lat"] = args.lat
        res["lon"] = args.lon
        _emit_json(res, args.out_json)

    elif args.command == "calibrate-azimuth":
        if not args.anchor and args.center_az is None:
            sys.stderr.write("error: calibrate-azimuth requires at least one of --anchor or --center-az\n")
            sys.exit(2)
        try:
            res = calibrate_azimuth(
                args.hfov_deg,
                anchors=args.anchor,
                projection=args.projection,
                center_az=args.center_az,
                x_fracs=args.x_frac,
                project_bearings=args.project_bearings,
            )
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            sys.exit(1)
        _emit_json(res, args.out_json)

    elif args.command == "admin-boundaries":
        res = query_admin_boundaries(args.lat, args.lon)
        _emit_json(res, args.out_json)

    elif args.command == "nearby-features":
        full_features = find_nearby_features(args.lat, args.lon, args.radius_km, verbose=bool(args.verbose or args.out_json))
        if not args.verbose and not args.out_json:
            base_features = [
                f for f in full_features
                if not (f.get("type") == "Stream" and float(f.get("distance_km", 0.0)) > 5.0)
            ]
        else:
            base_features = full_features
        has_filters = any(
            v is not None
            for v in (args.types, args.az_min, args.az_max, args.max_dist_mi, args.max_dist_km, args.search, args.max_results)
        )
        filtered = _filter_feature_list(
            base_features,
            types=args.types,
            az_min=args.az_min,
            az_max=args.az_max,
            max_dist_mi=args.max_dist_mi,
            max_dist_km=args.max_dist_km,
            search=args.search,
            max_results=args.max_results,
        )
        stdout_payload = _build_nearby_stdout(
            full_features,
            filtered,
            out_json_path=args.out_json,
            verbose=args.verbose,
            has_explicit_filters=has_filters,
        )
        _emit_json(full_features if args.out_json else filtered, args.out_json, stdout_data=stdout_payload)

    elif args.command == "elevation-profile":
        has_target_coords = (args.lat2 is not None and args.lon2 is not None)
        has_ray_target = (args.bearing_deg is not None and (args.dist_km is not None or args.dist_mi is not None))
        if not has_target_coords and not has_ray_target:
            sys.stderr.write(
                "error: elevation-profile requires either positional (lat2 lon2) OR (--bearing-deg with --dist-km or --dist-mi)\n"
            )
            sys.exit(2)
        if has_target_coords:
            tgt_lat, tgt_lon = args.lat2, args.lon2
            inc_prof = bool(args.full_profile)
        else:
            d_km = float(args.dist_km) if args.dist_km is not None else float(args.dist_mi) * 1.609344
            tgt_lat, tgt_lon = project_coordinate(args.lat1, args.lon1, args.bearing_deg, d_km)
            inc_prof = True
        res = compute_elevation_profile(
            args.lat1,
            args.lon1,
            tgt_lat,
            tgt_lon,
            samples=args.samples,
            include_profile=inc_prof,
        )
        _emit_json(res, args.out_json)

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
            projection=args.projection,
            check_coords=args.check_coords,
            image_width=args.image_width,
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
                include_skyline_lists=False,
            )
            res["synthetic_pano"] = pano_info
        res = _reorder_skyline_dict(res)

        has_sky_filters = any(v is not None for v in (args.types, args.max_dist_mi, args.max_dist_km, args.search))
        if has_sky_filters:
            filtered_res = dict(res)
            for list_key in ("visible_features_in_fov", "occluded_features_in_fov", "named_features_visibility"):
                if list_key in filtered_res:
                    filtered_res[list_key] = _filter_feature_list(
                        filtered_res[list_key],
                        types=args.types,
                        max_dist_mi=args.max_dist_mi,
                        max_dist_km=args.max_dist_km,
                        search=args.search,
                    )
            if any(v is not None for v in (args.max_dist_mi, args.max_dist_km, args.search)):
                for dem_key in ("dem_horizon_highpoints", "foreground_ridges"):
                    if dem_key in filtered_res:
                        filtered_res[dem_key] = _filter_feature_list(
                            filtered_res[dem_key],
                            max_dist_mi=args.max_dist_mi,
                            max_dist_km=args.max_dist_km,
                            search=args.search,
                        )
            filtered_res = _reorder_skyline_dict(filtered_res)
        else:
            filtered_res = res

        stdout_res = _build_skyline_stdout(filtered_res, out_json_path=args.out_json, verbose=args.verbose)
        _emit_json(res if args.out_json else filtered_res, args.out_json, stdout_data=stdout_res)

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
            include_skyline_lists=True,
        )
        if args.out_json and not args.verbose:
            stdout_pano = {
                "output_path": res.get("output_path"),
                "out_json": os.path.abspath(args.out_json),
                "az_start_deg": res.get("az_start_deg"),
                "az_span_deg": res.get("az_span_deg"),
                "alt_min_deg": res.get("alt_min_deg"),
                "alt_max_deg": res.get("alt_max_deg"),
                "annotated_features_count": res.get("annotated_features_count"),
                "visible_features_in_fov_count": len(res.get("visible_features_in_fov", [])),
                "dem_horizon_highpoints_count": len(res.get("dem_horizon_highpoints", [])),
                "foreground_ridges_count": len(res.get("foreground_ridges", [])),
            }
        else:
            stdout_pano = res
        _emit_json(res, args.out_json, stdout_data=stdout_pano)

    elif args.command == "render-topo":
        out_path = args.out_opt or args.out_pos
        if not out_path:
            sys.stderr.write("error: render-topo requires an output path via --out <path> or positional argument\n")
            sys.exit(2)
        res = render_topo_map(
            args.lat,
            args.lon,
            out_path=out_path,
            radius_km=args.radius_km,
            center_az=args.center_az,
            hfov_deg=args.hfov_deg,
            rays_str=args.rays,
        )
        _emit_json(res, args.out_json)


if __name__ == "__main__":
    main()
