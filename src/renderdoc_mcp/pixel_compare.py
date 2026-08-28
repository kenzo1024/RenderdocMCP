"""Compare RenderDoc-exported render-target images."""

import hashlib
import heapq
import os


def compare_roundtrip_targets(result, threshold=1):
    """Compare reset/applied PNGs and write one diff image per MRT."""
    before_key, after_key = result.get("comparison_pair", ["reset", "applied"])
    comparisons = []
    for slot in sorted(result.get("targets", {}).get(before_key, {})):
        before = result["targets"][before_key][slot]
        after = result["targets"].get(after_key, {}).get(slot)
        if not after:
            comparisons.append({"slot": int(slot), "status": "missing_applied_target"})
            continue
        comparisons.append(_compare_image_pair(before, after, threshold))
    return {
        "threshold": int(threshold),
        "comparison_pair": [before_key, after_key],
        "targets": comparisons,
        "status": "ok" if comparisons and all(x.get("status") == "ok" for x in comparisons) else "incomplete",
    }


def _compare_image_pair(before, after, threshold):
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError as exc:
        raise RuntimeError("Pillow is required for pixel comparison") from exc

    before_path = before["path"]
    after_path = after["path"]
    with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
        before_rgba = before_image.convert("RGBA")
        after_rgba = after_image.convert("RGBA")
        if before_rgba.size != after_rgba.size:
            return {
                "slot": before.get("slot"),
                "status": "size_mismatch",
                "before_size": before_rgba.size,
                "after_size": after_rgba.size,
            }
        diff = ImageChops.difference(before_rgba, after_rgba)
        diff_path = os.path.join(
            os.path.dirname(after_path),
            "%s_diff.png" % os.path.splitext(os.path.basename(after_path))[0],
        )
        diff.save(diff_path, format="PNG")

        threshold_mask = diff.point(lambda value: 255 if value > threshold else 0)
        changed = threshold_mask.getchannel(0)
        for channel in range(1, len(threshold_mask.getbands())):
            changed = ImageChops.lighter(changed, threshold_mask.getchannel(channel))
        histogram = changed.histogram()
        pixel_count = before_rgba.width * before_rgba.height
        differing_pixels = pixel_count - histogram[0]
        extrema = diff.getextrema()
        image_stats = ImageStat.Stat(diff)
        result = {
            "slot": before.get("slot"),
            "status": "ok",
            "width": before_rgba.width,
            "height": before_rgba.height,
            "pixel_count": pixel_count,
            "differing_pixels": differing_pixels,
            "differing_ratio": differing_pixels / max(pixel_count, 1),
            "max_channel_delta": max((item[1] for item in extrema), default=0),
            "mean_channel_delta": sum(image_stats.mean) / max(len(image_stats.mean), 1),
            "reset_sha256": _sha256(before_path),
            "applied_sha256": _sha256(after_path),
            "diff_path": os.path.normpath(diff_path),
            "candidate_pixels": _candidate_pixels(diff, threshold),
        }
        if before.get("raw_path") and after.get("raw_path"):
            result["raw"] = _compare_raw(before["raw_path"], after["raw_path"])
        return result


def _candidate_pixels(diff, threshold, limit=10, min_distance=16):
    """Return separated high-delta pixels suitable for shader debugging."""
    candidates = []
    keep = max(limit * 64, limit)
    width = diff.width
    pixels = (
        diff.get_flattened_data()
        if hasattr(diff, "get_flattened_data")
        else diff.getdata()
    )
    for index, channels in enumerate(pixels):
        score = max(channels)
        if score <= threshold:
            continue
        item = (score, index)
        if len(candidates) < keep:
            heapq.heappush(candidates, item)
        elif item > candidates[0]:
            heapq.heapreplace(candidates, item)

    selected = []
    min_distance_squared = min_distance * min_distance
    for score, index in sorted(candidates, reverse=True):
        x = index % width
        y = index // width
        if any(
            (x - item["x"]) ** 2 + (y - item["y"]) ** 2 < min_distance_squared
            for item in selected
        ):
            continue
        selected.append(
            {
                "x": x,
                "y": y,
                "max_channel_delta": score,
                "rgba_delta": list(diff.getpixel((x, y))),
            }
        )
        if len(selected) >= limit:
            break
    return selected


def _compare_raw(before_path, after_path):
    before_size = os.path.getsize(before_path)
    after_size = os.path.getsize(after_path)
    differing_bytes = 0
    max_byte_delta = 0
    compared_bytes = 0
    with open(before_path, "rb") as before_file, open(after_path, "rb") as after_file:
        while True:
            before_chunk = before_file.read(1024 * 1024)
            after_chunk = after_file.read(1024 * 1024)
            if not before_chunk and not after_chunk:
                break
            shared = min(len(before_chunk), len(after_chunk))
            compared_bytes += shared
            for left, right in zip(before_chunk[:shared], after_chunk[:shared]):
                if left != right:
                    differing_bytes += 1
                    max_byte_delta = max(max_byte_delta, abs(left - right))
            differing_bytes += abs(len(before_chunk) - len(after_chunk))
    return {
        "reset_size": before_size,
        "applied_size": after_size,
        "compared_bytes": compared_bytes,
        "differing_bytes": differing_bytes,
        "exact_match": before_size == after_size and differing_bytes == 0,
        "max_byte_delta": max_byte_delta,
        "reset_sha256": _sha256(before_path),
        "applied_sha256": _sha256(after_path),
    }


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
