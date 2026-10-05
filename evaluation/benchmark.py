"""Offline RES-02 planning and scoring. Never loads models or calls providers."""

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from functools import partial
from pathlib import Path

from validate import (
    ROOT,
    Invalid,
    check_schema,
    digest,
    parse,
    read_json,
    require,
    validate_dataset,
    validate_value,
)

VERSION = "res02-v1"
CURRENT_VERSION = "res02-v2"
VERSIONS = (VERSION, CURRENT_VERSION)
ASR_MODELS = (
    "groq/whisper-large-v3",
    "groq/whisper-large-v3-turbo",
    "vosk/small-en",
    "whisper.cpp/tiny.en",
    "whisper.cpp/base.en",
)
OCR_MODELS = ("ml-kit/latin-v2", "tesseract/eng-fast", "tesseract/eng-best")
ENTITY_TYPES = ("negation", "number", "name", "date", "unit")
RIGHTS_NOTICE = "Clip rows are authoritative; local acceptance is not rights clearance."


def obj(**properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def array(items, minimum=0, maximum=10000):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def integer(minimum=0, maximum=2**53 - 1):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def choice(*values):
    return {"type": "string", "enum": list(values)}


NOTE = {"type": "string", "minLength": 1, "pattern": r"\S"}
HASH = {"type": "string", "pattern": r"^[a-f0-9]{64}(?![\s\S])"}
ID = {"type": "string", "pattern": r"^[a-z0-9]+(?:-[a-z0-9]+)*(?![\s\S])"}
BOX = array(integer(0, 10000), 4, 4)
SEGMENT = obj(start_ms=integer(), end_ms=integer(1), text=NOTE)
RESOURCES = obj(
    duration_ms=integer(1),
    battery_start_permille=integer(0, 1000),
    battery_end_permille=integer(0, 1000),
    temperature_start_millicelsius=integer(-50000, 150000),
    temperature_end_millicelsius=integer(-50000, 150000),
    thermal_status_start=NOTE,
    thermal_status_end=NOTE,
    provenance=NOTE,
)
RESOURCES["type"] = ["object", "null"]


def input_schema(task, version=VERSION):
    require(version in VERSIONS, "schema_version", "unsupported scoring version")
    common = {
        "schema_version": {"const": version},
        "kind": choice("synthetic", "local-measurement"),
        "task": {"const": task},
        "run_id": ID,
        "clip_id": ID,
        "corpus_manifest_sha256": {
            "type": ["string", "null"],
            "pattern": HASH["pattern"],
        },
        "duration_ms": integer(1, 600000),
        "timebase": choice("media", "capture"),
        "model": choice(*(ASR_MODELS if task == "asr" else OCR_MODELS)),
        "model_revision": NOTE,
        "hardware": NOTE,
        "provenance": NOTE,
        "limitations": array(NOTE, 1, 100),
        "resources": RESOURCES,
        "hosted_processing_approval": {
            "type": ["string", "null"],
            "minLength": 1,
            "pattern": r"\S",
        },
    }
    if task == "asr":
        common.update(
            reference_basis=choice("media-reviewed-verbatim", "subtitle-reference"),
            timing_basis=choice("media-reviewed", "cue-envelope"),
            reference=array(SEGMENT, maximum=2000),
            entities=array(
                obj(
                    category=choice(*ENTITY_TYPES),
                    start_token=integer(),
                    end_token=integer(1),
                ),
                maximum=2000,
            ),
            chunks=array(
                obj(
                    seq=integer(),
                    start_ms=integer(),
                    end_ms=integer(1),
                    size_bytes=integer(1),
                    wall_ms=integer(),
                    status=choice("ok", "ASR_UNAVAILABLE"),
                    segments=array(SEGMENT, maximum=2000),
                ),
                1,
                1000,
            ),
            timestamp_pairs=array(
                obj(
                    reference_index=integer(),
                    chunk_seq=integer(),
                    segment_index=integer(),
                ),
                maximum=2000,
            ),
            limits=obj(
                file_cap_bytes=integer(1),
                minimum_audio_ms=integer(),
                audio_ms_per_hour=integer(1),
                requests_per_day=integer(1),
                provenance=NOTE,
            ),
        )
        common["limits"]["type"] = ["object", "null"]
    else:
        common.update(
            reference_basis=choice("full-screen-media-reviewed", "selected-regions"),
            iou_threshold_permille=integer(1, 1000),
            reference=array(
                obj(id=ID, start_ms=integer(), end_ms=integer(1), text=NOTE, box=BOX),
                maximum=2000,
            ),
            frames=array(
                obj(
                    id=ID,
                    presentation_ms=integer(),
                    sha256=HASH,
                    wall_ms=integer(),
                    status=choice("ok", "OCR_UNAVAILABLE"),
                    detections=array(obj(text=NOTE, box=BOX), maximum=500),
                ),
                1,
                10000,
            ),
            fixed_frame_ids=array(ID, 1, 120),
            fixed_tolerance_ms=integer(0, 1000),
            change_triggered_frame_ids=array(ID, maximum=10000),
        )
    return obj(**common)


def tokens(text, version=VERSION):
    """Case-fold and ignore punctuation, but retain numeric separators and signs."""
    require(version in VERSIONS, "schema_version", "unsupported scoring version")
    text = unicodedata.normalize("NFKC", text).casefold().replace("\u2019", "'")
    if version == CURRENT_VERSION:
        currency = "".join(sorted({c for c in text if unicodedata.category(c) == "Sc"}))
        symbols = re.escape("%°" + currency)
        return re.findall(
            rf"(?<!\w)[+-]\d+(?:[.,:/-]\d+)*|\d+(?:[.,:/-]\d+)*"
            rf"|[^\W\d_]+(?:'[^\W\d_]+)*|[{symbols}]",
            text,
        )
    return re.findall(r"[+-]?\d+(?:[.,:/-]\d+)*|[^\W\d_]+(?:'[^\W\d_]+)*|[%°]", text)


def align(reference, hypothesis):
    require(
        len(reference) <= 5000 and len(hypothesis) <= 5000,
        "text",
        "at most 5000 normalized tokens per clip",
    )
    width = len(hypothesis) + 1
    trace = bytearray((len(reference) + 1) * width)
    previous = list(range(width))
    for j in range(1, width):
        trace[j] = 2
    for i, word in enumerate(reference, 1):
        current = [i]
        trace[i * width] = 1
        for j, other in enumerate(hypothesis, 1):
            costs = (
                previous[j - 1] + (word != other),
                previous[j] + 1,
                current[j - 1] + 1,
            )
            operation = min(range(3), key=costs.__getitem__)
            current.append(costs[operation])
            trace[i * width + j] = operation
        previous = current
    i, j = len(reference), len(hypothesis)
    edits = []
    while i or j:
        operation = trace[i * width + j]
        if operation == 0:
            i, j = i - 1, j - 1
            edits.append(("equal" if reference[i] == hypothesis[j] else "substitution", i, j))
        elif operation == 1:
            i -= 1
            edits.append(("deletion", i, None))
        else:
            j -= 1
            edits.append(("insertion", i, j))
    return list(reversed(edits))


def error_counts(reference, hypothesis):
    edits = align(reference, hypothesis)
    counts = {
        name: sum(op == name for op, _, _ in edits)
        for name in ("substitution", "deletion", "insertion")
    }
    counts["reference_tokens"] = len(reference)
    counts["wer"] = (
        sum(counts[name] for name in ("substitution", "deletion", "insertion")) / len(reference)
        if reference
        else None
    )
    return counts, edits


def distribution(values):
    if not values:
        return None
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "p50": ordered[math.ceil(len(values) * 0.5) - 1],
        "p95": ordered[math.ceil(len(values) * 0.95) - 1],
        "max": ordered[-1],
    }


def interval(row, duration, location):
    require(
        0 <= row["start_ms"] < row["end_ms"] <= duration,
        location,
        "interval outside duration or reversed",
    )


def ordered_segments(rows, duration, location, version=VERSION, *, allow_unscorable=False):
    end = 0
    for row in rows:
        interval(row, duration, location)
        require(row["start_ms"] >= end, location, "segments overlap or are out of order")
        require(
            allow_unscorable or bool(tokens(row["text"], version)),
            location,
            "text has no scorable tokens",
        )
        end = row["end_ms"]


def score_asr(data):
    version = data["schema_version"]
    tokenize = partial(tokens, version=version)
    ordered_segments(data["reference"], data["duration_ms"], "reference", version)
    reference = [word for row in data["reference"] for word in tokenize(row["text"])]
    hypothesis, global_segments = [], {}
    ignored_unscorable = 0
    end = 0
    for seq, chunk in enumerate(data["chunks"]):
        interval(chunk, data["duration_ms"], "chunk")
        require(
            chunk["seq"] == seq and chunk["start_ms"] == end,
            "chunks",
            "must cover the clip contiguously from zero with consecutive seq; record failures",
        )
        end = chunk["end_ms"]
        ordered_segments(
            chunk["segments"],
            end - chunk["start_ms"],
            "chunk segments",
            version,
            allow_unscorable=version == CURRENT_VERSION,
        )
        require(
            chunk["status"] == "ok" or not chunk["segments"],
            "chunk",
            "ASR_UNAVAILABLE cannot carry successful segments",
        )
        if data["limits"] is not None:
            require(
                chunk["size_bytes"] <= data["limits"]["file_cap_bytes"],
                "chunk",
                "provider file cap exceeded",
            )
        for index, segment in enumerate(chunk["segments"]):
            words = tokenize(segment["text"])
            if not words:
                ignored_unscorable += 1
                continue
            hypothesis.extend(words)
            global_segments[(seq, index)] = {
                "start_ms": chunk["start_ms"] + segment["start_ms"],
                "end_ms": chunk["start_ms"] + segment["end_ms"],
            }
    require(end == data["duration_ms"], "chunks", "missing clip tail")
    counts, edits = error_counts(reference, hypothesis)
    entities = {category: {"reference_spans": 0, "incorrect_spans": 0} for category in ENTITY_TYPES}
    seen = set()
    for entity in data["entities"]:
        start, stop = entity["start_token"], entity["end_token"]
        key = (entity["category"], start, stop)
        require(
            0 <= start < stop <= len(reference) and key not in seen,
            "entities",
            "invalid or duplicate reference token span",
        )
        seen.add(key)
        incorrect = any(
            (op in {"substitution", "deletion"} and start <= i < stop)
            or (op == "insertion" and start < i < stop)
            for op, i, _ in edits
        )
        entities[entity["category"]]["reference_spans"] += 1
        entities[entity["category"]]["incorrect_spans"] += int(incorrect)
    for result in entities.values():
        result["error_rate"] = (
            result["incorrect_spans"] / result["reference_spans"]
            if result["reference_spans"]
            else None
        )
    require(
        not data["timestamp_pairs"] or data["timing_basis"] == "media-reviewed",
        "timestamp_pairs",
        "cue envelopes cannot establish timestamp drift",
    )
    starts, ends, seen_reference, seen_hypothesis = [], [], set(), set()
    for pair in data["timestamp_pairs"]:
        index = pair["reference_index"]
        key = (pair["chunk_seq"], pair["segment_index"])
        require(
            index < len(data["reference"]) and key in global_segments,
            "timestamp_pairs",
            "unknown reference or hypothesis segment",
        )
        require(
            index not in seen_reference and key not in seen_hypothesis,
            "timestamp_pairs",
            "pairs must be one-to-one",
        )
        seen_reference.add(index)
        seen_hypothesis.add(key)
        starts.append(global_segments[key]["start_ms"] - data["reference"][index]["start_ms"])
        ends.append(global_segments[key]["end_ms"] - data["reference"][index]["end_ms"])
    quota = None
    if data["limits"] is not None:
        limits = data["limits"]
        audio_ms = sum(
            max(c["end_ms"] - c["start_ms"], limits["minimum_audio_ms"]) for c in data["chunks"]
        )
        quota = {
            "attempted_requests": len(data["chunks"]),
            "estimated_audio_ms": audio_ms,
            "hourly_audio_fraction": audio_ms / limits["audio_ms_per_hour"],
            "daily_request_fraction": len(data["chunks"]) / limits["requests_per_day"],
            "is_three_minute_clip": data["duration_ms"] == 180000,
            "actual_consumption_verified": False,
        }
    return {
        "word_errors": counts,
        **(
            {"ignored_unscorable_segments": ignored_unscorable}
            if version == CURRENT_VERSION
            else {}
        ),
        "critical_entities": entities,
        "failed_chunks": sum(c["status"] != "ok" for c in data["chunks"]),
        "wall_ms_per_chunk": distribution([c["wall_ms"] for c in data["chunks"]]),
        "timestamp_drift_ms": {
            "paired_segments": len(starts),
            "unpaired_reference_segments": len(data["reference"]) - len(starts),
            "start_signed": distribution(starts),
            "end_signed": distribution(ends),
            "absolute_endpoints": distribution([abs(v) for v in starts + ends]),
        },
        "quota_estimate": quota,
        "timebase_relative_segments": [
            {"chunk_seq": seq, "segment_index": index, **segment}
            for (seq, index), segment in global_segments.items()
        ],
    }


def box_valid(box):
    require(box[0] < box[2] and box[1] < box[3], "box", "box has no positive area")


def iou(left, right):
    intersection = max(0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0, min(left[3], right[3]) - max(left[1], right[1])
    )
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / (left_area + right_area - intersection)


def sampling_result(reference, frames, recognized):
    temporal = {
        row["id"]
        for row in reference
        if any(row["start_ms"] <= f["presentation_ms"] < row["end_ms"] for f in frames)
    }
    found = set().union(*(recognized[f["id"]] for f in frames)) if frames else set()
    total = len(reference)
    return {
        "frames": len(frames),
        "reference_occurrences": total,
        "temporally_missed": total - len(temporal),
        "temporal_miss_rate": (total - len(temporal)) / total if total else None,
        "not_exactly_recognized": total - len(found),
        "recognition_miss_rate": (total - len(found)) / total if total else None,
    }


def score_ocr(data):
    tokenize = partial(tokens, version=data["schema_version"])
    reference, frames = data["reference"], data["frames"]
    ids = set()
    for row in reference:
        interval(row, data["duration_ms"], "reference")
        box_valid(row["box"])
        require(
            row["id"] not in ids and bool(tokenize(row["text"])),
            "reference",
            "duplicate occurrence id or unscorable text",
        )
        ids.add(row["id"])
    times, frame_ids, recognized, tracks, active_tracks = [], set(), {}, [], []
    totals = {"substitution": 0, "deletion": 0, "insertion": 0, "reference_tokens": 0}
    unmatched_detections = 0
    for frame in frames:
        time = frame["presentation_ms"]
        require(
            time < data["duration_ms"] and (not times or time > times[-1]),
            "frames",
            "presentation times must increase within the clip",
        )
        require(frame["id"] not in frame_ids, "frames", "duplicate frame id")
        require(
            frame["status"] == "ok" or not frame["detections"],
            "frames",
            "OCR_UNAVAILABLE cannot carry successful detections",
        )
        frame_ids.add(frame["id"])
        times.append(time)
        for detection in frame["detections"]:
            box_valid(detection["box"])
            require(
                bool(tokenize(detection["text"])),
                "detection",
                "text has no scorable tokens",
            )
        visible = [row for row in reference if row["start_ms"] <= time < row["end_ms"]]
        candidates = sorted(
            (-iou(row["box"], detection["box"]), i, j)
            for i, row in enumerate(visible)
            for j, detection in enumerate(frame["detections"])
            if iou(row["box"], detection["box"]) * 1000 >= data["iou_threshold_permille"]
        )
        matches, used = {}, set()
        for _, i, j in candidates:
            if i not in matches and j not in used:
                matches[i] = j
                used.add(j)
        recognized[frame["id"]] = set()
        for i, row in enumerate(visible):
            expected = tokenize(row["text"])
            actual = tokenize(frame["detections"][matches[i]]["text"]) if i in matches else []
            counts, _ = error_counts(expected, actual)
            for name in totals:
                totals[name] += counts[name]
            if expected == actual:
                recognized[frame["id"]].add(row["id"])
        for j, detection in enumerate(frame["detections"]):
            if j not in used:
                unmatched_detections += 1
                if data["reference_basis"] == "full-screen-media-reviewed":
                    totals["insertion"] += len(tokenize(detection["text"]))
        next_tracks = []
        for detection in frame["detections"]:
            normalized = tokenize(detection["text"])
            candidates = [
                index
                for index in active_tracks
                if index not in next_tracks
                and tracks[index]["normalized_tokens"] == normalized
                and iou(tracks[index]["observations"][-1]["box"], detection["box"]) * 1000
                >= data["iou_threshold_permille"]
            ]
            if candidates:
                index = max(
                    candidates,
                    key=lambda n: iou(tracks[n]["observations"][-1]["box"], detection["box"]),
                )
            else:
                index = len(tracks)
                tracks.append({"normalized_tokens": normalized, "observations": []})
            tracks[index]["observations"].append(
                {
                    "frame_id": frame["id"],
                    "presentation_ms": time,
                    "box": detection["box"],
                }
            )
            next_tracks.append(index)
        active_tracks = next_tracks
    requested = data["change_triggered_frame_ids"]
    require(
        len(set(requested)) == len(requested) and set(requested) <= frame_ids,
        "change_triggered_frame_ids",
        "duplicate or unknown frame id",
    )
    fixed_ids = data["fixed_frame_ids"]
    by_id = {frame["id"]: frame for frame in frames}
    fixed_times = list(range(0, data["duration_ms"], 5000))
    require(
        len(fixed_ids) == len(fixed_times)
        and len(set(fixed_ids)) == len(fixed_ids)
        and set(fixed_ids) <= frame_ids,
        "fixed_frame_ids",
        "missing, duplicate or unknown fixed 5-second sample",
    )
    for tick, frame_id in zip(fixed_times, fixed_ids, strict=True):
        require(
            tick <= by_id[frame_id]["presentation_ms"] <= tick + data["fixed_tolerance_ms"],
            "fixed_frame_ids",
            "sample outside declared fixed cadence tolerance",
        )
    totals["wer"] = (
        sum(totals[k] for k in ("substitution", "deletion", "insertion"))
        / totals["reference_tokens"]
        if totals["reference_tokens"]
        else None
    )
    frame_identity = json.dumps(
        [(f["id"], f["presentation_ms"], f["sha256"]) for f in frames],
        separators=(",", ":"),
    ).encode()
    return {
        "frame_set_sha256": hashlib.sha256(frame_identity).hexdigest(),
        "frame_weighted_word_errors": totals,
        "unmatched_detections": unmatched_detections,
        "failed_frames": sum(f["status"] != "ok" for f in frames),
        "wall_ms_per_frame": distribution([f["wall_ms"] for f in frames]),
        "fixed_5s": sampling_result(reference, [by_id[key] for key in fixed_ids], recognized),
        "change_triggered": sampling_result(
            reference, [f for f in frames if f["id"] in requested], recognized
        ),
        "deduplicated_tracks": tracks,
    }


def corpus(directory, media_root=None):
    manifest = read_json(directory / "dataset.json")
    require(
        isinstance(manifest, dict) and manifest.get("kind") in ("frozen-local", "frozen"),
        "corpus",
        "use an explicit local or full frozen snapshot, not a historical draft",
    )
    validate_dataset(
        directory,
        local_frozen=manifest["kind"] == "frozen-local",
        frozen=manifest["kind"] == "frozen",
        media_root=media_root,
    )
    with (directory / "clips.jsonl").open(encoding="utf-8", newline="\n") as stream:
        clips = [parse(line, "clips") for line in stream]
    return manifest, clips


def plan(directory):
    manifest, clips = corpus(directory)
    return {
        "schema_version": VERSION,
        "status": "inventory_only",
        "dataset_version": manifest["dataset_version"],
        "corpus_manifest_sha256": digest(directory / "dataset.json"),
        "dev_clip_ids": [c["clip_id"] for c in clips if c["split"] == "dev"],
        "excluded_holdout_ids": [c["clip_id"] for c in clips if c["split"] == "test"],
        "asr_candidates": ASR_MODELS,
        "ocr_candidates": OCR_MODELS,
        "proposed_pcm_trials": [
            {
                "chunk_ms": ms,
                "requests_per_3min": 180000 // ms,
                "pcm_wav_bytes_per_full_chunk": 44 + ms * 32,
            }
            for ms in (10000, 15000)
        ],
        "existing_evidence": {
            "subtitle_references": "Existing reviewed/source SRTs reused for private trials; "
            "subtitle agreement, not independently certified verbatim speech.",
            "nonphone_comparisons": "Hosted ASR, critical spans, Tesseract and source-video "
            "sampling recorded privately under #92; see BENCHMARKS.md.",
            "capture_feasibility": "AN-01 #9 closed; Galaxy A21s / Android 12 matrix in "
            "docs/compatibility.md. Not recognizer performance evidence.",
        },
        "remaining_evidence": {
            "phone_recognition_resources": "Vosk/whisper.cpp viability, ML Kit same-captured-frame "
            "comparison, runtime and three-minute battery/thermal readings.",
            "capture_chunks": "Actual AN-04 chunk format, sizes, offsets and capture clock; "
            "workstation 10/15-second PCM is not device output.",
            "timing_and_visibility": "Independently reviewed speech timing and full-screen "
            "visibility/boxes for final timing and missed-text claims.",
            "selection": "Reviewed final adapter, explicit fallback and sampling decisions.",
        },
        "new_run_requirements": [
            "Controlled dev media and recorded observations; reuse existing references.",
            "Separate permission and dated account limits before any new hosted run.",
        ],
        "local_artifacts_verified": False,
        "limitations": manifest.get("limitations", []),
        "rights_clearance": {
            c["clip_id"]: c["rights"].get("clearance", "not-recorded") for c in clips
        },
        "rights_notice": RIGHTS_NOTICE,
        "media_bytes_verified": False,
        "hosted_processing_authorized_by_plan": False,
    }


def score(data, directory, media_root=None):
    require(
        isinstance(data, dict) and data.get("task") in ("asr", "ocr"),
        "input",
        "expected ASR or OCR benchmark object",
    )
    schema = input_schema(data["task"], data.get("schema_version"))
    check_schema(schema)
    validate_value(data, schema, "benchmark")
    # JSON Schema accepts integral floats; convert only after validating their exact type/range.
    data = integral_numbers(data)
    if data["kind"] == "local-measurement":
        manifest, clips = corpus(directory, media_root)
        require(
            data["corpus_manifest_sha256"] == digest(directory / "dataset.json"),
            "corpus",
            "manifest hash mismatch",
        )
        clip = next((c for c in clips if c["clip_id"] == data["clip_id"]), None)
        require(
            clip is not None and clip["split"] == "dev",
            "clip",
            "only known dev clips may be scored; holdout evaluation is not authorized here",
        )
        require(clip["duration_ms"] == data["duration_ms"], "clip", "duration mismatch")
        require(
            data["timebase"] == "media",
            "clip",
            "local corpus uses media time; live capture needs a separately aligned reference",
        )
        if data["model"].startswith("groq/"):
            require(
                data["hosted_processing_approval"] is not None,
                "approval",
                "hosted observations require their own opaque approval reference",
            )
    else:
        require(
            data["corpus_manifest_sha256"] is None,
            "synthetic",
            "must not claim a corpus hash",
        )
        require(
            media_root is None,
            "synthetic",
            "media verification is for local observations only",
        )
    if data["model"].startswith("groq/"):
        require(
            data["limits"] is not None,
            "limits",
            "hosted ASR requires dated provider limits",
        )
    metrics = score_asr(data) if data["task"] == "asr" else score_ocr(data)
    resources = data["resources"]
    if resources is not None:
        require(
            resources["duration_ms"] == data["duration_ms"],
            "resources",
            "measurement interval must equal the clip duration",
        )
    return {
        "schema_version": data["schema_version"],
        "run_id": data["run_id"],
        "kind": data["kind"],
        "clip_id": data["clip_id"],
        "corpus_manifest_sha256": data["corpus_manifest_sha256"],
        "task": data["task"],
        "model": data["model"],
        "model_revision": data["model_revision"],
        "hardware": data["hardware"],
        "timebase": data["timebase"],
        "reference_basis": data["reference_basis"],
        "provenance": data["provenance"],
        "reference_sha256": hashlib.sha256(
            json.dumps(
                {
                    key: data[key]
                    for key in (
                        "reference",
                        "reference_basis",
                        "timebase",
                        "entities",
                        "timing_basis",
                        *(("schema_version",) if data["schema_version"] == CURRENT_VERSION else ()),
                    )
                    if key in data
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "limitations": data["limitations"],
        "metrics": metrics,
        "resource_observation": resources,
        "decision": "pending_measurements_and_review",
        "media_bytes_verified": media_root is not None,
        "rights_notice": RIGHTS_NOTICE,
        "corpus_limitations": manifest.get("limitations", [])
        if data["kind"] == "local-measurement"
        else ["Synthetic observations; no corpus measurement."],
    }


def integral_numbers(value):
    if isinstance(value, dict):
        return {key: integral_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [integral_numbers(item) for item in value]
    return int(value) if type(value) is float else value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus-local")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "plan", help="show dev-only inputs and missing evidence; no model execution"
    )
    schema_command = commands.add_parser("schema", help="emit the strict scoring input schema")
    schema_command.add_argument("task", choices=("asr", "ocr"))
    schema_command.add_argument("--version", choices=VERSIONS, default=VERSION)
    scoring = commands.add_parser("score", help="score a controlled local observation file")
    scoring.add_argument("input", type=Path)
    scoring.add_argument("--media-root", type=Path, help="also verify corpus media bytes locally")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            result = input_schema(args.task, args.version)
        elif args.command == "plan":
            result = plan(args.corpus)
        else:
            result = score(read_json(args.input), args.corpus, args.media_root)
            result["input_sha256"] = digest(args.input)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.command == "score" and (
            result["metrics"].get("failed_chunks", 0) or result["metrics"].get("failed_frames", 0)
        ):
            return 1
    except (Invalid, OSError, UnicodeError, RecursionError) as error:
        print(f"RES-02 failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
