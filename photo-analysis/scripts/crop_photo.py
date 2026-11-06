import os
import re
import sys
import json
import math
import shutil
import argparse
from PIL import Image, ImageDraw, ImageFont

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _sanitize_tag(label):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(label).strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "custom"


def _parse_box_tokens(tokens):
    """Parse 4 tokens (X1, Y1, X2, Y2[:LABEL]) into (x1, y1, x2, y2, label)."""
    if len(tokens) != 4:
        raise ValueError(f"--box requires 4 arguments (X1 Y1 X2 Y2[:LABEL]), got {len(tokens)}")
    t0, t1, t2, t3 = tokens
    label = None
    if isinstance(t3, str) and ":" in t3:
        y2_str, label_raw = t3.split(":", 1)
        label = label_raw.strip() if label_raw.strip() else None
    else:
        y2_str = t3
    x1 = float(t0)
    y1 = float(t1)
    x2 = float(t2)
    y2 = float(y2_str)
    return (x1, y1, x2, y2), label


def _resolve_box_px(box_coords, w, h, label=None):
    x1, y1, x2, y2 = [float(v) for v in box_coords]
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.0:
        x1, y1, x2, y2 = round(x1 * w), round(y1 * h), round(x2 * w), round(y2 * h)
    else:
        x1, y1, x2, y2 = round(x1), round(y1), round(x2), round(y2)

    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    bx1 = max(0, min(w, int(x1)))
    by1 = max(0, min(h, int(y1)))
    bx2 = max(0, min(w, int(x2)))
    by2 = max(0, min(h, int(y2)))

    if bx2 <= bx1 or by2 <= by1:
        raise ValueError(
            f"Invalid or empty bounding box after clamping to {w}x{h}: ({bx1}, {by1}, {bx2}, {by2})"
        )

    box = (bx1, by1, bx2, by2)
    tag = _sanitize_tag(label) if label else f"box_{bx1}_{by1}_{bx2}_{by2}"
    return box, tag


def _resolve_region_box(region, w, h):
    reg = region.lower().strip()
    if reg == "center":
        box = (int(round(w * 0.25)), int(round(h * 0.25)), int(round(w * 0.75)), int(round(h * 0.75)))
    elif reg == "top-left":
        box = (0, 0, int(round(w * 0.5)), int(round(h * 0.5)))
    elif reg == "top-right":
        box = (int(round(w * 0.5)), 0, w, int(round(h * 0.5)))
    elif reg == "bottom-left":
        box = (0, int(round(h * 0.5)), int(round(w * 0.5)), h)
    elif reg == "bottom-right":
        box = (int(round(w * 0.5)), int(round(h * 0.5)), w, h)
    elif reg == "left":
        box = (0, 0, int(round(w * 0.5)), h)
    elif reg == "right":
        box = (int(round(w * 0.5)), 0, w, h)
    elif reg == "top":
        box = (0, 0, w, int(round(h * 0.5)))
    elif reg == "bottom":
        box = (0, int(round(h * 0.5)), w, h)
    else:
        raise ValueError(
            f"Unknown region: {region}. Choose from center, top-left, top-right, "
            "bottom-left, bottom-right, left, right, top, bottom."
        )
    return box, reg.replace("-", "_")


def _resolve_slice_box(idx, total, w, h):
    if total < 1 or idx < 1 or idx > total:
        raise ValueError(f"Invalid slice {idx} of {total} (must satisfy 1 <= INDEX <= TOTAL)")
    slice_w = w / float(total)
    x1 = int(round((idx - 1) * slice_w))
    x2 = int(round(idx * slice_w))
    return (x1, 0, max(x1 + 1, x2), h), f"slice_{idx}_of_{total}"


def _choose_grid_steps(span_frac, inner_px, min_label_sep_px):
    """Choose (line_step, label_step) in full-image fractional units."""
    if span_frac <= 0.0:
        return 0.05, 0.05

    if span_frac <= 0.06:
        candidates = [0.01, 0.02, 0.05]
    elif span_frac <= 0.16:
        candidates = [0.02, 0.05, 0.10]
    else:
        candidates = [0.05, 0.10, 0.20, 0.25]

    line_step = candidates[0]
    for step in candidates:
        px_step = (step / span_frac) * inner_px
        if px_step >= 24.0:
            line_step = step
            break

    label_step = candidates[-1]
    for step in candidates:
        if step < line_step - 1e-9:
            continue
        px_step = (step / span_frac) * inner_px
        if px_step >= min_label_sep_px:
            label_step = step
            break

    return line_step, label_step


def _generate_grid_fractions(f_min, f_max, step):
    """Return sorted fractional coordinates strictly within (f_min, f_max) at multiples of step."""
    eps = 1e-7
    start_k = int(math.floor((f_min + eps) / step)) + 1
    end_k = int(math.ceil((f_max - eps) / step)) - 1
    vals = []
    for k in range(start_k, end_k + 1):
        val = round(k * step, 4)
        if f_min + eps < val < f_max - eps:
            vals.append(val)
    return vals


def _draw_dashed_vline(img, x, y_start, y_end, is_major=True):
    """Draw a subtle 1px alpha-blended dashed vertical line without occluding fine features."""
    px = img.load()
    period = 14 if is_major else 12
    alpha = 0.50 if is_major else 0.35
    inv_alpha = 1.0 - alpha
    for y in range(y_start, y_end):
        phase = (y - y_start) % period
        if phase < 4:
            # Dark dash
            r, g, b = px[x, y]
            px[x, y] = (
                int(r * inv_alpha + 35 * alpha),
                int(g * inv_alpha + 35 * alpha),
                int(b * inv_alpha + 35 * alpha),
            )
        elif 4 <= phase < 7:
            # Light dash (ensures visibility over dark shadows/forest)
            r, g, b = px[x, y]
            px[x, y] = (
                int(r * inv_alpha + 235 * alpha),
                int(g * inv_alpha + 235 * alpha),
                int(b * inv_alpha + 235 * alpha),
            )


def _draw_dashed_hline(img, y, x_start, x_end, is_major=True):
    """Draw a subtle 1px alpha-blended dashed horizontal line without occluding fine features."""
    px = img.load()
    period = 14 if is_major else 12
    alpha = 0.50 if is_major else 0.35
    inv_alpha = 1.0 - alpha
    for x in range(x_start, x_end):
        phase = (x - x_start) % period
        if phase < 4:
            r, g, b = px[x, y]
            px[x, y] = (
                int(r * inv_alpha + 35 * alpha),
                int(g * inv_alpha + 35 * alpha),
                int(b * inv_alpha + 35 * alpha),
            )
        elif 4 <= phase < 7:
            r, g, b = px[x, y]
            px[x, y] = (
                int(r * inv_alpha + 235 * alpha),
                int(g * inv_alpha + 235 * alpha),
                int(b * inv_alpha + 235 * alpha),
            )


def _text_width(draw, text, font):
    if hasattr(draw, "textbbox"):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    return len(text) * 6


def apply_margin_grid(cropped_img, box, orig_w, orig_h):
    """
    Draw subtle 1px dashed coordinate gridlines across `cropped_img` and place all
    fractional + pixel coordinate labels inside a white outer border collar (36px top/bottom,
    58px left/right) so text never occludes image features.
    """
    inner_w, inner_h = cropped_img.size
    bx1, by1, bx2, by2 = box
    fx1, fx2 = bx1 / float(orig_w), bx2 / float(orig_w)
    fy1, fy2 = by1 / float(orig_h), by2 / float(orig_h)
    dfx = max(1e-6, fx2 - fx1)
    dfy = max(1e-6, fy2 - fy1)

    top_pad = 36
    bottom_pad = 36
    left_pad = 58
    right_pad = 58

    canvas_w = inner_w + left_pad + right_pad
    canvas_h = inner_h + top_pad + bottom_pad
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(cropped_img, (left_pad, top_pad))

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    # Subtle 1px border around the inner image inside the collar
    draw.rectangle(
        [left_pad - 1, top_pad - 1, left_pad + inner_w, top_pad + inner_h],
        outline=(110, 110, 110),
        width=1,
    )

    x_line_step, x_label_step = _choose_grid_steps(dfx, inner_w, min_label_sep_px=48.0)
    y_line_step, y_label_step = _choose_grid_steps(dfy, inner_h, min_label_sep_px=28.0)

    x_fracs = _generate_grid_fractions(fx1, fx2, x_line_step)
    y_fracs = _generate_grid_fractions(fy1, fy2, y_line_step)

    def _draw_x_collar_label(cx_pos, fx_val):
        gx_pos = left_pad + cx_pos
        orig_x_px = int(round(fx_val * orig_w))
        lbl_frac = f"x={fx_val:.2f}"
        lbl_px = f"({orig_x_px}px)"
        w1 = _text_width(draw, lbl_frac, font)
        w2 = _text_width(draw, lbl_px, font)
        tx1 = max(2, min(canvas_w - w1 - 2, gx_pos - w1 // 2))
        tx2 = max(2, min(canvas_w - w2 - 2, gx_pos - w2 // 2))
        draw.text((tx1, 4), lbl_frac, fill=(20, 20, 20), font=font)
        draw.text((tx2, 17), lbl_px, fill=(80, 80, 80), font=font)
        draw.text((tx1, top_pad + inner_h + 5), lbl_frac, fill=(20, 20, 20), font=font)
        draw.text((tx2, top_pad + inner_h + 18), lbl_px, fill=(80, 80, 80), font=font)

    def _draw_y_collar_label(cy_pos, fy_val):
        gy_pos = top_pad + cy_pos
        orig_y_px = int(round(fy_val * orig_h))
        lbl_frac = f"y={fy_val:.2f}"
        lbl_px = f"({orig_y_px}px)"
        w1 = _text_width(draw, lbl_frac, font)
        w2 = _text_width(draw, lbl_px, font)
        ty1 = max(top_pad - 4, min(top_pad + inner_h - 18, gy_pos - 11))
        ty2 = ty1 + 12
        lx1 = max(2, left_pad - 7 - w1)
        lx2 = max(2, left_pad - 7 - w2)
        draw.text((lx1, ty1), lbl_frac, fill=(20, 20, 20), font=font)
        draw.text((lx2, ty2), lbl_px, fill=(80, 80, 80), font=font)
        rx = left_pad + inner_w + 7
        draw.text((rx, ty1), lbl_frac, fill=(20, 20, 20), font=font)
        draw.text((rx, ty2), lbl_px, fill=(80, 80, 80), font=font)

    # Draw vertical gridlines and top/bottom outer collar labels
    labeled_cxs = []
    for fx in x_fracs:
        cx = int(round(((fx - fx1) / dfx) * inner_w))
        if cx <= 0 or cx >= inner_w:
            continue
        gx = left_pad + cx
        rem = abs(round(fx / x_label_step) * x_label_step - fx)
        is_major = rem < 1e-4

        _draw_dashed_vline(canvas, gx, top_pad, top_pad + inner_h, is_major=is_major)

        # Tick marks in top and bottom margins
        tick_len = 4 if is_major else 2
        draw.line([(gx, top_pad - tick_len - 1), (gx, top_pad - 1)], fill=(60, 60, 60), width=1)
        draw.line(
            [(gx, top_pad + inner_h + 1), (gx, top_pad + inner_h + tick_len + 1)],
            fill=(60, 60, 60),
            width=1,
        )

        if is_major and (not labeled_cxs or (cx - labeled_cxs[-1]) >= 46):
            _draw_x_collar_label(cx, fx)
            labeled_cxs.append(cx)

    if not labeled_cxs or labeled_cxs[0] >= 52:
        _draw_x_collar_label(0, fx1)
    if not labeled_cxs or (inner_w - labeled_cxs[-1]) >= 52:
        _draw_x_collar_label(inner_w, fx2)

    # Draw horizontal gridlines and left/right outer collar labels
    labeled_cys = []
    for fy in y_fracs:
        cy = int(round(((fy - fy1) / dfy) * inner_h))
        if cy <= 0 or cy >= inner_h:
            continue
        gy = top_pad + cy
        rem = abs(round(fy / y_label_step) * y_label_step - fy)
        is_major = rem < 1e-4

        _draw_dashed_hline(canvas, gy, left_pad, left_pad + inner_w, is_major=is_major)

        # Tick marks in left and right margins
        tick_len = 4 if is_major else 2
        draw.line([(left_pad - tick_len - 1, gy), (left_pad - 1, gy)], fill=(60, 60, 60), width=1)
        draw.line(
            [(left_pad + inner_w + 1, gy), (left_pad + inner_w + tick_len + 1, gy)],
            fill=(60, 60, 60),
            width=1,
        )

        if is_major and (not labeled_cys or (cy - labeled_cys[-1]) >= 26):
            _draw_y_collar_label(cy, fy)
            labeled_cys.append(cy)

    if not labeled_cys or labeled_cys[0] >= 30:
        _draw_y_collar_label(0, fy1)
    if not labeled_cys or (inner_h - labeled_cys[-1]) >= 30:
        _draw_y_collar_label(inner_h, fy2)

    return canvas


def _downscale_if_needed(img, max_dim):
    if not max_dim or max_dim <= 0:
        return img
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = float(max_dim) / float(max(w, h))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    return img.resize((new_w, new_h), resample=resample)


def _resolve_output_paths(photo_path, out_path, tag, multi_output=False, label=None):
    base_name = os.path.splitext(os.path.basename(photo_path))[0]
    if not out_path:
        scratch_dir = os.path.join(os.getcwd(), "scratch")
        if not os.path.exists(scratch_dir):
            try:
                os.makedirs(scratch_dir, exist_ok=True)
            except Exception:
                scratch_dir = os.getcwd()
        target = os.path.join(scratch_dir, f"{base_name}_{tag}.jpg")
        return target, None, target if label is not None else None

    if os.path.isdir(out_path) or out_path.endswith(("/", "\\")):
        os.makedirs(out_path, exist_ok=True)
        target = os.path.join(out_path, f"{base_name}_{tag}.jpg")
        return target, None, target if label is not None else None

    parent_dir = os.path.dirname(os.path.abspath(out_path))
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    stem, ext = os.path.splitext(out_path)
    if not ext:
        ext = ".jpg"

    if not multi_output:
        if label is not None:
            stem_base = os.path.basename(stem)
            if not stem_base.lower().endswith(f"_{tag.lower()}"):
                labeled_path = f"{stem}_{tag}{ext}"
                out_base_lower = os.path.basename(out_path).lower()
                is_generic = (
                    out_base_lower in ("_crop.jpg", "_box.jpg", "_crop.png", "_box.png")
                    or out_base_lower.endswith(("_crop.jpg", "_box.jpg", "_crop.png", "_box.png", "_crop.jpeg", "_box.jpeg"))
                    or stem_base.lower() == base_name.lower()
                )
                if is_generic:
                    return labeled_path, out_path, labeled_path
                else:
                    return out_path, labeled_path, labeled_path
            return out_path, None, out_path
        return out_path, None, None

    target = f"{stem}_{tag}{ext}"
    return target, None, target if label is not None else None


def _resolve_output_path(photo_path, out_path, tag, multi_output=False, label=None):
    target_path, _, _ = _resolve_output_paths(
        photo_path, out_path, tag, multi_output=multi_output, label=label
    )
    return target_path


def _save_single_op(img, orig_w, orig_h, photo_path, box, tag, out_path, multi_output=False, max_dim=None, grid=False, label=None):
    cropped = img.crop(box)
    if max_dim:
        cropped = _downscale_if_needed(cropped, max_dim)
    inner_w, inner_h = cropped.size

    if grid:
        final_img = apply_margin_grid(cropped, box, orig_w, orig_h)
    else:
        final_img = cropped

    target_path, secondary_path, labeled_path = _resolve_output_paths(
        photo_path, out_path, tag, multi_output=multi_output, label=label
    )
    ext = os.path.splitext(target_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        final_img.save(target_path, quality=92)
    else:
        final_img.save(target_path)

    abs_out = os.path.abspath(target_path)
    abs_secondary = None
    if secondary_path:
        abs_secondary = os.path.abspath(secondary_path)
        if abs_secondary != abs_out:
            shutil.copyfile(abs_out, abs_secondary)

    bx1, by1, bx2, by2 = box
    result = {
        "out_path": abs_out,
        "tag": tag,
        "crop_box_px": [bx1, by1, bx2, by2],
        "crop_box_fraction": [
            round(bx1 / float(orig_w), 4),
            round(by1 / float(orig_h), 4),
            round(bx2 / float(orig_w), 4),
            round(by2 / float(orig_h), 4),
        ],
        "original_size": [orig_w, orig_h],
        "inner_crop_size": [inner_w, inner_h],
        "output_size": [final_img.size[0], final_img.size[1]],
        "grid": bool(grid),
    }
    if label:
        result["label"] = label
    if labeled_path and secondary_path:
        result["labeled_out_path"] = os.path.abspath(labeled_path)
        result["alternate_out_path"] = abs_secondary
    return result


def crop_image(
    photo_path,
    box_coords=None,
    region=None,
    slice_info=None,
    out_path=None,
    grid=False,
    max_dim=None,
    overview=None,
    all_slices=None,
):
    """
    Backward-compatible programmatic entrypoint returning `(abs_out_path, (out_w, out_h))`
    for a single operation.
    """
    results = execute_crop_operations(
        photo_path=photo_path,
        box_list=[box_coords] if box_coords is not None else None,
        region=region,
        slice_info=slice_info,
        all_slices=all_slices,
        overview=overview,
        max_dim=max_dim,
        grid=grid,
        out_path=out_path,
    )
    first = results[0]
    return first["out_path"], tuple(first["output_size"])


def execute_crop_operations(
    photo_path,
    box_list=None,
    region=None,
    slice_info=None,
    all_slices=None,
    overview=None,
    max_dim=None,
    grid=False,
    out_path=None,
):
    if not os.path.exists(photo_path):
        raise FileNotFoundError(f"Photo not found: {photo_path}")

    img = Image.open(photo_path)
    w, h = img.size
    if img.mode != "RGB":
        img = img.convert("RGB")

    ops = []

    if overview is not None:
        ov_dim = max_dim if max_dim is not None else (int(overview) if int(overview) > 0 else 2000)
        ops.append({
            "box": (0, 0, w, h),
            "tag": "overview",
            "max_dim": ov_dim,
            "label": None,
        })

    if all_slices is not None:
        total = int(all_slices)
        if total < 1:
            raise ValueError(f"--all-slices must be >= 1, got {total}")
        for idx in range(1, total + 1):
            s_box, s_tag = _resolve_slice_box(idx, total, w, h)
            ops.append({
                "box": s_box,
                "tag": s_tag,
                "max_dim": max_dim,
                "label": None,
            })

    if slice_info is not None:
        idx, total = int(slice_info[0]), int(slice_info[1])
        s_box, s_tag = _resolve_slice_box(idx, total, w, h)
        ops.append({
            "box": s_box,
            "tag": s_tag,
            "max_dim": max_dim,
            "label": None,
        })

    if region is not None:
        r_box, r_tag = _resolve_region_box(region, w, h)
        ops.append({
            "box": r_box,
            "tag": r_tag,
            "max_dim": max_dim,
            "label": None,
        })

    if box_list:
        for item in box_list:
            if isinstance(item, (list, tuple)) and len(item) == 4 and any(isinstance(x, str) for x in item):
                coords, label = _parse_box_tokens(item)
            elif isinstance(item, dict):
                coords = item["coords"]
                label = item.get("label")
            else:
                coords = item
                label = None
            b_box, b_tag = _resolve_box_px(coords, w, h, label=label)
            ops.append({
                "box": b_box,
                "tag": b_tag,
                "max_dim": max_dim,
                "label": label,
            })

    if not ops:
        # Default: center 50%
        c_box, c_tag = _resolve_region_box("center", w, h)
        ops.append({
            "box": c_box,
            "tag": c_tag,
            "max_dim": max_dim,
            "label": None,
        })

    # Ensure unique tags when multiple operations are requested
    multi_output = len(ops) > 1
    seen_tags = {}
    for op in ops:
        t = op["tag"]
        if t in seen_tags:
            seen_tags[t] += 1
            op["tag"] = f"{t}_{seen_tags[t]}"
        else:
            seen_tags[t] = 1

    results = []
    for op in ops:
        res = _save_single_op(
            img=img,
            orig_w=w,
            orig_h=h,
            photo_path=photo_path,
            box=op["box"],
            tag=op["tag"],
            out_path=out_path,
            multi_output=multi_output,
            max_dim=op["max_dim"],
            grid=grid,
            label=op["label"],
        )
        results.append(res)

    return results


def main():
    parser = argparse.ArgumentParser(description="Crop high-resolution photos for detail inspection.")
    parser.add_argument("photo_path", help="Path to input photo")
    parser.add_argument(
        "--region",
        choices=["center", "top-left", "top-right", "bottom-left", "bottom-right", "left", "right", "top", "bottom"],
        help="Predefined region",
    )
    parser.add_argument(
        "--box",
        nargs=4,
        action="append",
        type=str,
        metavar=("X1", "Y1", "X2", "Y2[:LABEL]"),
        help="Bounding box (pixels or fractions 0.0-1.0), repeatable",
    )
    parser.add_argument(
        "--slice",
        nargs=2,
        type=int,
        metavar=("INDEX", "TOTAL"),
        help="Slice horizontally (e.g. 1 3 for 1st of 3 slices)",
    )
    parser.add_argument(
        "--all-slices",
        "--slices",
        dest="all_slices",
        type=int,
        metavar="N",
        help="Generate all 1..N horizontal slices in a single command",
    )
    parser.add_argument(
        "--overview",
        nargs="?",
        const=2000,
        default=None,
        type=int,
        metavar="MAX_DIM",
        help="Save a downscaled full-frame overview image (default max dimension: 2000 px)",
    )
    parser.add_argument(
        "--max-dim",
        type=int,
        default=None,
        metavar="MAX_DIM",
        help="Maximum width/height in pixels for output image(s)",
    )
    parser.add_argument(
        "--grid",
        action="store_true",
        help="Draw subtle 1px dashed coordinate gridlines with full-image fractional and pixel labels in an outer margin collar",
    )
    parser.add_argument("--out", help="Output file path (optional)")

    args = parser.parse_args()

    try:
        results = execute_crop_operations(
            photo_path=args.photo_path,
            box_list=args.box,
            region=args.region,
            slice_info=args.slice,
            all_slices=args.all_slices,
            overview=args.overview,
            max_dim=args.max_dim,
            grid=args.grid,
            out_path=args.out,
        )
        payload = dict(results[0])
        if len(results) > 1:
            payload["count"] = len(results)
            payload["outputs"] = results
        print(json.dumps(payload, indent=2))
    except Exception as e:
        sys.stderr.write(f"Error cropping image: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
