import os
import sys
import argparse
from PIL import Image

def crop_image(photo_path, box_coords=None, region=None, slice_info=None, out_path=None):
    if not os.path.exists(photo_path):
        raise FileNotFoundError(f"Photo not found: {photo_path}")

    img = Image.open(photo_path)
    w, h = img.size

    if box_coords:
        # box_coords can be integers (pixels) or floats <= 1.0 (percentages)
        x1, y1, x2, y2 = box_coords
        if max(x1, y1, x2, y2) <= 1.0:
            x1, y1, x2, y2 = int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)
        box = (max(0, int(x1)), max(0, int(y1)), min(w, int(x2)), min(h, int(y2)))
        tag = f"box_{box[0]}_{box[1]}_{box[2]}_{box[3]}"
    elif slice_info:
        # e.g. slice 2 of 4 (1-indexed)
        idx, total = slice_info
        slice_w = w / total
        x1 = int((idx - 1) * slice_w)
        x2 = int(idx * slice_w)
        box = (x1, 0, x2, h)
        tag = f"slice_{idx}_of_{total}"
    elif region:
        reg = region.lower()
        if reg == "center":
            box = (int(w * 0.25), int(h * 0.25), int(w * 0.75), int(h * 0.75))
        elif reg == "top-left":
            box = (0, 0, int(w * 0.5), int(h * 0.5))
        elif reg == "top-right":
            box = (int(w * 0.5), 0, w, int(h * 0.5))
        elif reg == "bottom-left":
            box = (0, int(h * 0.5), int(w * 0.5), h)
        elif reg == "bottom-right":
            box = (int(w * 0.5), int(h * 0.5), w, h)
        elif reg == "left":
            box = (0, 0, int(w * 0.5), h)
        elif reg == "right":
            box = (int(w * 0.5), 0, w, h)
        elif reg == "top":
            box = (0, 0, w, int(h * 0.5))
        elif reg == "bottom":
            box = (0, int(h * 0.5), w, h)
        else:
            raise ValueError(f"Unknown region: {region}. Choose from center, top-left, top-right, bottom-left, bottom-right, left, right, top, bottom.")
        tag = reg.replace("-", "_")
    else:
        # Default: center 50%
        box = (int(w * 0.25), int(h * 0.25), int(w * 0.75), int(h * 0.75))
        tag = "center"

    cropped = img.crop(box)

    if not out_path:
        base_name = os.path.splitext(os.path.basename(photo_path))[0]
        # Store in scratch dir if available, or current working dir
        scratch_dir = os.path.join(os.getcwd(), "scratch")
        if not os.path.exists(scratch_dir):
            try:
                os.makedirs(scratch_dir, exist_ok=True)
            except Exception:
                scratch_dir = os.getcwd()
        out_path = os.path.join(scratch_dir, f"{base_name}_{tag}.jpg")

    cropped.save(out_path, quality=92)
    return os.path.abspath(out_path), cropped.size

def main():
    parser = argparse.ArgumentParser(description="Crop high-resolution photos for detail inspection.")
    parser.add_argument("photo_path", help="Path to input photo")
    parser.add_argument("--region", choices=["center", "top-left", "top-right", "bottom-left", "bottom-right", "left", "right", "top", "bottom"], help="Predefined region")
    parser.add_argument("--box", nargs=4, type=float, metavar=("X1", "Y1", "X2", "Y2"), help="Bounding box (pixels or fractions 0.0-1.0)")
    parser.add_argument("--slice", nargs=2, type=int, metavar=("INDEX", "TOTAL"), help="Slice horizontally (e.g. 1 3 for 1st of 3 slices)")
    parser.add_argument("--out", help="Output file path (optional)")

    args = parser.parse_args()

    try:
        out_file, dims = crop_image(
            args.photo_path,
            box_coords=args.box,
            region=args.region,
            slice_info=args.slice,
            out_path=args.out
        )
        print(f"Cropped saved to: {out_file} (Dimensions: {dims[0]}x{dims[1]})")
    except Exception as e:
        sys.stderr.write(f"Error cropping image: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
