"""Local-only OCR primitives. Models and media must already exist locally."""

import io
import math
import time

from benchmark import box_valid, error_counts, tokens
from validate import require


def normalized_box(box, width, height):
    require(width > 0 and height > 0, "image", "empty dimensions")
    require(
        0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height, "box", "outside image"
    )
    return [
        round(box[0] * 10000 / width),
        round(box[1] * 10000 / height),
        round(box[2] * 10000 / width),
        round(box[3] * 10000 / height),
    ]


def selected_region_errors(regions, words, width, height):
    """Assign full-frame OCR words by their center, without cropping or re-OCR."""
    seen = set()
    for region in regions:
        normalized_box(region["box"], width, height)
        require(
            region["id"] not in seen and bool(tokens(region["text"])),
            "regions",
            "duplicate ID or empty reference",
        )
        seen.add(region["id"])
    for index, region in enumerate(regions):
        a = region["box"]
        for other in regions[:index]:
            b = other["box"]
            require(
                min(a[2], b[2]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[1], b[1]),
                "regions",
                "overlapping references could double-count words",
            )
    assigned = {region["id"]: [] for region in regions}
    unmatched = 0
    for word in words:
        box = word["box"]
        normalized_box(box, width, height)
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        matches = [
            r for r in regions if r["box"][0] <= x < r["box"][2] and r["box"][1] <= y < r["box"][3]
        ]
        if matches:
            assigned[matches[0]["id"]].append(word["text"])
        else:
            unmatched += 1
    counts = {"substitution": 0, "deletion": 0, "insertion": 0, "reference_tokens": 0}
    details = []
    for region in regions:
        actual = " ".join(assigned[region["id"]])
        errors, _ = error_counts(tokens(region["text"]), tokens(actual))
        for key in counts:
            counts[key] += errors[key]
        details.append({"region_id": region["id"], "hypothesis": actual, "word_errors": errors})
    counts["wer"] = (
        sum(counts[k] for k in ("substitution", "deletion", "insertion"))
        / counts["reference_tokens"]
        if counts["reference_tokens"]
        else None
    )
    return {"word_errors": counts, "regions": details, "unmatched_words": unmatched}


def recognize(api, image):
    """Retain native reading order, confidence and pixel boxes from a full frame."""
    import tesserocr

    started = time.perf_counter()
    api.SetImage(image)
    api.Recognize()
    words = []
    iterator = api.GetIterator()
    if iterator is not None:
        while True:
            if not iterator.Empty(tesserocr.RIL.WORD):
                text = iterator.GetUTF8Text(tesserocr.RIL.WORD)
                box = iterator.BoundingBox(tesserocr.RIL.WORD)
                if text and tokens(text):
                    require(box is not None, "OCR", "word has no box")
                    box_valid(box)
                    words.append(
                        {
                            "text": text.strip(),
                            "box": list(box),
                            "confidence": iterator.Confidence(tesserocr.RIL.WORD),
                        }
                    )
            if not iterator.Next(tesserocr.RIL.WORD):
                break
    return {"words": words, "wall_ms": round((time.perf_counter() - started) * 1000)}


def changed(previous, current, threshold=12):
    require(len(previous) == len(current) and len(current) > 0, "pixels", "size mismatch")
    require(threshold > 0, "threshold", "must be positive")
    return sum(abs(a - b) for a, b in zip(previous, current, strict=True)) >= threshold * len(
        current
    )


def window_coverage(windows, times):
    """Coverage of supplied windows, not proof text was recognized or fully annotated."""
    require(len(set(times)) == len(times) and times == sorted(times), "samples", "invalid times")
    missed = []
    for window in windows:
        require(0 <= window["start_ms"] < window["end_ms"], "window", "invalid interval")
        if not any(window["start_ms"] <= t < window["end_ms"] for t in times):
            missed.append(window["id"])
    return {
        "windows": len(windows),
        "missed_window_ids": missed,
        "miss_rate": len(missed) / len(windows) if windows else None,
    }


def sample_video(path, duration_ms, output):
    """1 Hz workstation probe; fixed 5 s versus pixel-change + 5 s heartbeat."""
    import av
    from PIL import Image

    require(duration_ms > 0, "video", "duration must be positive")
    output.mkdir(parents=True, exist_ok=False)
    frames, fixed, adaptive = [], [], []
    next_probe = next_fixed = 0
    anchor = None
    last_change = -5000
    previous_time = -1
    with av.open(str(path)) as container:
        require(len(container.streams.video) == 1, "video", "expected one video track")
        for frame in container.decode(video=0):
            require(frame.pts is not None, "video", "missing presentation time")
            time_ms = round(frame.pts * frame.time_base * 1000)
            require(time_ms >= previous_time, "video", "nonmonotonic timestamps")
            previous_time = time_ms
            if time_ms < next_probe:
                continue
            if time_ms >= duration_ms:
                break
            require(time_ms - next_probe < 1000, "video", "missing 1 Hz probe")
            next_probe += 1000
            image = frame.to_image().convert("RGB")
            factor = 720 / max(image.size)
            image = image.resize(
                (round(image.width * factor), round(image.height * factor)),
                Image.Resampling.LANCZOS,
            )
            pixels = bytes(image.convert("L").resize((64, 64), Image.Resampling.BILINEAR).tobytes())
            select_fixed = time_ms >= next_fixed
            select_change = (
                anchor is None or time_ms - last_change >= 5000 or changed(anchor, pixels)
            )
            if not select_fixed and not select_change:
                continue
            frame_id = f"frame-{time_ms}"
            if select_fixed:
                require(time_ms - next_fixed < 1000, "video", "missing fixed sample")
                fixed.append(frame_id)
                next_fixed += 5000
            if select_change:
                adaptive.append(frame_id)
                anchor, last_change = pixels, time_ms
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=72)
            file = output / f"{frame_id}.jpg"
            file.write_bytes(buffer.getvalue())
            frames.append(
                {
                    "id": frame_id,
                    "presentation_ms": time_ms,
                    "path": str(file),
                    "width": image.width,
                    "height": image.height,
                }
            )
    require(len(fixed) == math.ceil(duration_ms / 5000), "video", "incomplete fixed coverage")
    require(duration_ms - previous_time <= 1000, "video", "incomplete 1 Hz probe coverage")
    return {
        "frames": frames,
        "fixed_frame_ids": fixed,
        "change_triggered_frame_ids": adaptive,
        "unavailable_final_probe_ms": next_probe if next_probe < duration_ms else None,
        "last_decoded_frame_ms": previous_time,
        "policy": {
            "probe_ms": 1000,
            "fixed_ms": 5000,
            "change_mean_luma_threshold": 12,
            "thumbnail": [64, 64],
            "heartbeat_ms": 5000,
            "jpeg_quality": 72,
            "long_edge": 720,
        },
        "limitation": "Workstation source-video simulation, not a measured Android trigger.",
    }
