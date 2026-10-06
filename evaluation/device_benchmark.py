"""RES-02b on-device benchmark helpers (#103).

Host-side tooling for the phone replay harness in `device-harness/`. It
prepares replay lists from the immutable RES-02 handoff bundle, converts raw
phone output into strict `res02-v1` observations using the bundle's existing
references and selected-entity labels unchanged, scores them with
`benchmark.score`, verifies real AN-04 chunk ZIPs and summarizes resource
logs. It never runs a hosted model, uploads media or modifies the bundle.
Outputs are written to new files only (existing files are never replaced).
"""

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from asr_experiment import subtitle_timing  # noqa: E402
from benchmark import VERSION, distribution, score, tokens  # noqa: E402
from validate import Invalid, require  # noqa: E402

GROQ_MODELS = ("whisper-large-v3", "whisper-large-v3-turbo")
CHUNK_LENGTHS = (10000, 15000)
PCM_BYTES_PER_MS = 32
LIVE_MS = 180000
DEVICE_LIMITATIONS = [
    "Subtitle agreement only, not certified verbatim ASR ground truth.",
    "Hypothesis segments are whole-chunk text envelopes for lexical scoring; raw recognizer "
    "timestamps are audited separately and never clamped.",
    "Replay of existing workstation-derived 16 kHz PCM16 chunks on the phone, not AN-04 "
    "device-captured audio.",
    "Native command-line process started through adb shell, not the app process; Android "
    "scheduling and memory limits for an app may differ.",
    "Selected lexical entity spans only, not exhaustive critical-error ground truth.",
]


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk_id(clip_id, chunk_ms, seq):
    return f"{clip_id}-{chunk_ms}-{seq:03d}"


def bundle_index(bundle):
    return read_json(Path(bundle) / "input-index.json")


def asr_list(bundle, device_dir):
    """One replay list for all 142 chunks, ordered by clip, schedule and seq."""
    lines = []
    for clip in bundle_index(bundle)["clips"]:
        for trial in clip["asr_trials"]:
            for chunk in trial["chunks"]:
                name = Path(chunk["path"]).name
                lines.append(
                    f"{chunk_id(clip['clip_id'], trial['chunk_ms'], chunk['seq'])}\t"
                    f"{device_dir.rstrip('/')}/{name}"
                )
    return "\n".join(lines) + "\n"


def read_run(path):
    """Parse a driver JSON-lines file; returns (init, chunks by id, end).

    Vosk embeds its own pretty-printed result objects, so rows are decoded as a
    stream of JSON values rather than strictly one per physical line.
    """
    init, end, chunks = None, None, {}
    text = Path(path).read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    position, number = 0, 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            break
        row, position = decoder.raw_decode(text, position)
        number += 1
        if row["type"] == "init":
            require(init is None, f"{path}:{number}", "duplicate init row")
            init = row
        elif row["type"] == "chunk":
            require(row["id"] not in chunks, f"{path}:{number}", "duplicate chunk id")
            chunks[row["id"]] = row
        elif row["type"] == "end":
            end = row
    require(init is not None, str(path), "missing init row")
    return init, chunks, end


NON_SPEECH = re.compile(r"^\s*[\[(][^\])]*[\])]\s*$")


def is_annotation(text):
    """A whole segment such as `[BLANK_AUDIO]` or `(music)`: a non-speech marker, not words."""
    return bool(NON_SPEECH.match(text))


def raw_segments(row, chunk_ms):
    """Recognizer segments in seconds relative to the chunk, as the driver reported them."""
    segments = []
    if row.get("status") != "ok":
        return segments
    if "segments" in row:
        for segment in row["segments"]:
            segments.append(
                {
                    "start": segment["start_ms"] / 1000,
                    "end": segment["end_ms"] / 1000,
                    "text": segment["text"].strip(),
                    **({"annotation": True} if is_annotation(segment["text"]) else {}),
                }
            )
    else:
        for result in row["results"]:
            words = result.get("result") or []
            text = result.get("text", "").strip()
            if not text:
                continue
            if words:
                segments.append({"start": words[0]["start"], "end": words[-1]["end"], "text": text})
            else:
                segments.append({"start": 0.0, "end": chunk_ms / 1000, "text": text, "untimed": True})
    return segments


def timestamp_faults(segments, length_ms):
    """Raw invalid or out-of-chunk segments, reported without clamping."""
    faults = {"reversed_or_empty": 0, "negative_start": 0, "end_after_chunk": 0}
    for segment in segments:
        if segment.get("untimed"):
            continue
        if segment["end"] <= segment["start"]:
            faults["reversed_or_empty"] += 1
        if segment["start"] < 0:
            faults["negative_start"] += 1
        if segment["end"] * 1000 > length_ms:
            faults["end_after_chunk"] += 1
    return faults


def chunk_text(segments):
    return " ".join(s["text"] for s in segments if s["text"] and not s.get("annotation")).strip()


def template_path(bundle, clip_id, chunk_ms):
    return (
        Path(bundle)
        / "evidence"
        / "res02-selected-entity-scores"
        / f"{clip_id}-{chunk_ms}-whisper-large-v3-selected-entities.input.json"
    )


def observation(template, model, revision, hardware, provenance, chunks, run_id, limitations):
    """Replace only the hypothesis side of an existing scorer input."""
    data = dict(template)
    data.update(
        run_id=run_id,
        model=model,
        model_revision=revision,
        hardware=hardware,
        provenance=provenance,
        limitations=limitations,
        resources=None,
        hosted_processing_approval=None,
        limits=None,
        timestamp_pairs=[],
        chunks=chunks,
    )
    return data


def asr_score(args):
    bundle = Path(args.bundle)
    init, rows, end = read_run(args.run)
    index = bundle_index(bundle)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    per_setting = {}
    for clip in index["clips"]:
        for trial in clip["asr_trials"]:
            chunk_ms = trial["chunk_ms"]
            template = read_json(template_path(bundle, clip["clip_id"], chunk_ms))
            require(
                [(c["seq"], c["start_ms"], c["end_ms"]) for c in template["chunks"]]
                == [(c["seq"], c["start_ms"], c["end_ms"]) for c in trial["chunks"]],
                clip["clip_id"],
                "template chunk boundaries differ from the bundle index",
            )
            observed, timing_chunks, faults, missing = [], [], [], []
            for chunk in trial["chunks"]:
                key = chunk_id(clip["clip_id"], chunk_ms, chunk["seq"])
                row = rows.get(key)
                length = chunk["end_ms"] - chunk["start_ms"]
                if row is None:
                    missing.append(key)
                    row = {"status": "ASR_UNAVAILABLE", "wall_ms": 0, "error": "not_attempted"}
                segments = raw_segments(row, length)
                text = chunk_text(segments)
                ok = row["status"] == "ok"
                observed.append(
                    {
                        "seq": chunk["seq"],
                        "start_ms": chunk["start_ms"],
                        "end_ms": chunk["end_ms"],
                        "size_bytes": chunk["size_bytes"],
                        "wall_ms": round(row.get("wall_ms", 0)),
                        "status": "ok" if ok else "ASR_UNAVAILABLE",
                        "segments": [{"start_ms": 0, "end_ms": length, "text": text}]
                        if ok and tokens(text, VERSION)
                        else [],
                    }
                )
                timed = [s for s in segments if not s.get("untimed") and not s.get("annotation")]
                timing_chunks.append(
                    {"seq": chunk["seq"], "start_ms": chunk["start_ms"], "end_ms": chunk["end_ms"],
                     "segments": [s for s in timed if s["end"] >= s["start"]]}
                )
                faults.append({"seq": chunk["seq"], **timestamp_faults(segments, length),
                               "annotations": sum(1 for s in segments if s.get("annotation"))})
            require(not missing or args.allow_missing, "run", f"chunks not attempted: {missing[:5]}")
            run_id = f"{clip['clip_id']}-{chunk_ms}-{args.label}"
            data = observation(
                template, args.model, args.model_revision, args.hardware,
                f"{args.provenance}; driver output SHA256 {sha256_file(args.run)}; "
                f"template {template_path(bundle, clip['clip_id'], chunk_ms).name}",
                observed, run_id, DEVICE_LIMITATIONS,
            )
            name = f"{clip['clip_id']}-{chunk_ms}-{args.label}"
            write_new(out / f"{name}.input.json", data)
            result = score(data, Path(args.corpus))
            result["input_sha256"] = sha256_file(out / f"{name}.input.json")
            write_new(out / f"{name}.score.json", result)
            timing = subtitle_timing(template["reference"], timing_chunks)
            timing["raw_fault_counts"] = faults
            write_new(out / f"{name}.timing.json", timing)
            per_setting.setdefault(chunk_ms, []).append(
                {"clip_id": clip["clip_id"], "score": result, "timing": timing,
                 "walls": [c["wall_ms"] for c in observed],
                 "lengths": [c["end_ms"] - c["start_ms"] for c in observed],
                 "missing": missing}
            )
    summary = {
        "model": args.model,
        "model_revision": args.model_revision,
        "hardware": args.hardware,
        "label": args.label,
        "driver_output_sha256": sha256_file(args.run),
        "init": {k: init.get(k) for k in ("status", "init_ms", "rss_kib", "peak_rss_kib", "threads")},
        "end": end,
        "settings": [aggregate(chunk_ms, items, rows) for chunk_ms, items in sorted(per_setting.items())],
        "limitations": DEVICE_LIMITATIONS,
    }
    write_new(out / "summary.json", summary)
    print(json.dumps({"out": str(out), "settings": summary["settings"]}, indent=2)[:4000])
    return 0


def sum_entities(scores):
    totals = {}
    for result in scores:
        for category, row in result["metrics"]["critical_entities"].items():
            item = totals.setdefault(category, {"reference_spans": 0, "incorrect_spans": 0})
            item["reference_spans"] += row["reference_spans"]
            item["incorrect_spans"] += row["incorrect_spans"]
    for item in totals.values():
        spans = item["reference_spans"]
        item["error_rate"] = item["incorrect_spans"] / spans if spans else None
    return totals


def sum_edits(scores):
    edits = {"substitution": 0, "deletion": 0, "insertion": 0, "reference_tokens": 0}
    for result in scores:
        for key in edits:
            edits[key] += result["metrics"]["word_errors"][key]
    errors = edits["substitution"] + edits["deletion"] + edits["insertion"]
    edits["wer"] = errors / edits["reference_tokens"] if edits["reference_tokens"] else None
    return edits


def aggregate(chunk_ms, items, rows):
    scores = [i["score"] for i in items]
    walls = [w for i in items for w in i["walls"]]
    rtf = [w / max(1, n) for i in items for w, n in zip(i["walls"], i["lengths"], strict=True)]
    timing = [i["timing"] for i in items]
    faults = {"reversed_or_empty": 0, "negative_start": 0, "end_after_chunk": 0}
    annotations = 0
    for t in timing:
        for row in t["raw_fault_counts"]:
            annotations += row.get("annotations", 0)
            for key in faults:
                faults[key] += row[key]
    matched = [m for t in timing for m in t["matches"]]
    return {
        "chunk_ms": chunk_ms,
        "clips": len(items),
        "chunks": len(walls),
        "failed_chunks": sum(r["metrics"]["failed_chunks"] for r in scores),
        "not_attempted": sum(len(i["missing"]) for i in items),
        "non_speech_annotation_segments": annotations,
        "non_speech_annotation_note": "Whole bracketed/parenthesized segments (for example "
        "[BLANK_AUDIO]) are excluded from lexical scoring and counted here.",
        "word_errors": sum_edits(scores),
        "critical_entities": sum_entities(scores),
        "wall_ms_per_chunk": distribution(walls),
        "real_time_factor": distribution([round(x * 1000) for x in rtf]),
        "real_time_factor_note": "wall_ms / chunk audio ms, in permille",
        "timestamps": {
            "recognizer_segments": sum(t["provider_segments"] for t in timing),
            "matched_whole_cue_segments": len(matched),
            "absolute_cue_endpoint_delta_ms": distribution(
                [abs(m[k]) for m in matched for k in ("start_delta_ms", "end_delta_ms")]
            ),
            "raw_faults": faults,
            "basis": "Exact whole-cue subtitle anchors only; not independently reviewed speech timing.",
        },
        "per_clip": [
            {
                "clip_id": i["clip_id"],
                "word_errors": i["score"]["metrics"]["word_errors"],
                "failed_chunks": i["score"]["metrics"]["failed_chunks"],
                "wall_ms_per_chunk": i["score"]["metrics"]["wall_ms_per_chunk"],
            }
            for i in items
        ],
    }


def groq_baseline(args):
    """Aggregate the existing Groq selected-entity scores the same way (no new calls)."""
    directory = Path(args.bundle) / "evidence" / "res02-selected-entity-scores"
    result = []
    for model in GROQ_MODELS:
        for chunk_ms in CHUNK_LENGTHS:
            scores = []
            for path in sorted(directory.glob(f"*-{chunk_ms}-{model}-selected-entities.score.json")):
                if path.name.split(f"-{chunk_ms}-")[1] != f"{model}-selected-entities.score.json":
                    continue
                scores.append(read_json(path))
            walls = []
            for s in scores:
                inp = read_json(directory / path_input(s))
                walls.extend(c["wall_ms"] for c in inp["chunks"])
            result.append(
                {
                    "model": f"groq/{model}",
                    "chunk_ms": chunk_ms,
                    "clips": len(scores),
                    "chunks": len(walls),
                    "failed_chunks": sum(s["metrics"]["failed_chunks"] for s in scores),
                    "word_errors": sum_edits(scores),
                    "critical_entities": sum_entities(scores),
                    "wall_ms_per_chunk": distribution(walls),
                    "basis": "Existing #92 hosted responses and scores from the handoff bundle; "
                    "workstation client over the network, not phone latency.",
                }
            )
    out = Path(args.out)
    write_new(out, {"comparisons": result})
    print(json.dumps(result, indent=2)[:3000])
    return 0


def path_input(score_row):
    return f"{score_row['run_id']}.input.json"


# ---- AN-04 chunk verification ------------------------------------------------------------


def verify_capture(args):
    """Check a pulled capture directory against the AN-04 rules, using real bytes."""
    root = Path(args.capture)
    manifest = read_json(root / "capture.json")
    report = {"manifest_sha256": sha256_file(root / "capture.json"), "checks": [], "chunks": []}

    def check(name, ok, detail=None):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})

    check("manifest_version", manifest.get("manifestVersion") == 2, manifest.get("manifestVersion"))
    check("timebase_capture", manifest.get("timebase") == "capture")
    grid = manifest.get("chunkDurationMs")
    check("chunk_grid_ms", grid == args.chunk_ms, grid)
    duration = manifest["durationMs"]
    if not manifest.get("finished") and getattr(args, "in_progress", False):
        # A snapshot taken while recording: the manifest has no duration yet, so the sealed
        # chunks are checked up to the last sealed end; the final seal is not covered.
        duration = max((c["endMs"] for c in manifest["chunks"]), default=0)
        report["in_progress_snapshot"] = True
    check("duration_within_live_limit", 0 < duration <= LIVE_MS, duration)
    chunks = sorted(manifest["chunks"], key=lambda c: c["seq"])
    seqs = [c["seq"] for c in chunks]
    skipped, evicted = manifest.get("skippedSeqs", []), manifest.get("evictedSeqs", [])
    expected = list(range(0, math.ceil(duration / grid))) if duration else []
    check("seq_contiguous_with_skips", sorted(seqs + skipped) == expected,
          {"seqs": seqs, "skipped": skipped, "expected_last": expected[-1] if expected else None})
    drift = []
    for chunk in chunks:
        seq = chunk["seq"]
        start, end = seq * grid, min((seq + 1) * grid, LIVE_MS)
        is_last = seq == max(seqs)
        row = {"seq": seq, "start_ms": chunk["startMs"], "end_ms": chunk["endMs"],
               "size_bytes": chunk["sizeBytes"], "audio_bytes": chunk["audioBytes"],
               "frames": len(chunk["frameOffsetsMs"]), "present": False}
        row["grid_ok"] = chunk["startMs"] == start and (
            chunk["endMs"] == end or (is_last and start < chunk["endMs"] <= end)
        )
        row["frame_offsets_in_interval"] = all(
            chunk["startMs"] <= f < chunk["endMs"] for f in chunk["frameOffsetsMs"]
        )
        path = root / "chunks" / chunk["file"] if (root / "chunks").is_dir() else root / chunk["file"]
        if path.exists():
            row["present"] = True
            data = path.read_bytes()
            row["sha256_ok"] = hashlib.sha256(data).hexdigest() == chunk["sha256"]
            row["size_ok"] = len(data) == chunk["sizeBytes"]
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                meta = json.loads(archive.read("chunk.json"))
                pcm = archive.read("audio-16000-mono-s16le.pcm") if (
                    "audio-16000-mono-s16le.pcm" in names) else b""
                row["zip_entries"] = names
                row["zip_entry_times_epoch"] = all(
                    i.date_time[:3] == (1980, 1, 1) for i in archive.infolist()
                )
            row["chunk_json_matches"] = (
                meta["seq"] == seq and meta["start_ms"] == chunk["startMs"]
                and meta["end_ms"] == chunk["endMs"] and meta["timebase"] == "capture"
            )
            row["pcm_bytes"] = len(pcm)
            row["pcm_matches_declared"] = (meta.get("audio") or {}).get("bytes") == len(pcm)
            nominal = (chunk["endMs"] - chunk["startMs"]) * PCM_BYTES_PER_MS
            row["pcm_minus_nominal_ms"] = (len(pcm) - nominal) / PCM_BYTES_PER_MS
            drift.append(row["pcm_minus_nominal_ms"])
            row["text_observations"] = len(meta.get("text_observations", []))
            row["recognizer"] = meta.get("recognizer")
            row["sampling"] = meta.get("sampling")
            for cap_name, cap in (("groq_25mb", 25_000_000), ("backend_upload_cap", args.upload_cap)):
                row[f"within_{cap_name}"] = len(data) <= cap
        report["chunks"].append(row)
    present = [r for r in report["chunks"] if r["present"]]
    check("chunks_present", len(present) == len(chunks), {"present": len(present), "listed": len(chunks),
                                                          "evicted": evicted})
    for key in ("grid_ok", "frame_offsets_in_interval"):
        check(key, all(r[key] for r in report["chunks"]))
    for key in ("sha256_ok", "size_ok", "chunk_json_matches", "pcm_matches_declared",
                "zip_entry_times_epoch", "within_groq_25mb", "within_backend_upload_cap"):
        check(key, all(r.get(key) for r in present))
    report["gaps"] = manifest.get("gaps", [])
    report["stop_reason"] = manifest.get("stopReason")
    report["duration_ms"] = duration
    report["final_chunk"] = report["chunks"][-1] if report["chunks"] else None
    report["pcm_minus_nominal_ms"] = distribution([round(d) for d in drift]) if drift else None
    report["pcm_minus_nominal_total_ms"] = sum(drift)
    report["max_chunk_bytes"] = max((r["size_bytes"] for r in report["chunks"]), default=None)
    report["playback_signal"] = manifest.get("playbackSignalDetected")
    report["frames"] = manifest.get("frames")
    report["dropped_frames"] = manifest.get("droppedFrames")
    report["ok"] = all(c["ok"] for c in report["checks"])
    write_new(args.out, report)
    print(json.dumps({k: report[k] for k in ("ok", "checks", "duration_ms", "pcm_minus_nominal_total_ms",
                                             "max_chunk_bytes", "gaps")}, indent=2))
    return 0 if report["ok"] else 1


def concat_capture_pcm(root, manifest):
    pcm = bytearray()
    starts = []
    for chunk in sorted(manifest["chunks"], key=lambda c: c["seq"]):
        with zipfile.ZipFile(Path(root) / "chunks" / chunk["file"]) as archive:
            starts.append((chunk["seq"], chunk["startMs"], len(pcm) // 2))
            pcm += archive.read("audio-16000-mono-s16le.pcm")
    return bytes(pcm), starts


def align_capture(args):
    """Estimate capture-to-media offset and clock ratio by cross-correlating real audio.

    Uses envelope cross-correlation of short windows between the captured PCM and the
    bundle's source PCM (concatenated 10-second WAV chunks for the clip). Nothing is
    assumed: if the correlation peak is weak the window is reported as unaligned.
    """
    import numpy as np

    root = Path(args.capture)
    manifest = read_json(root / "capture.json")
    captured, _ = concat_capture_pcm(root, manifest)
    cap = np.frombuffer(captured, dtype="<i2").astype(np.float32)
    clip = next(c for c in bundle_index(args.bundle)["clips"] if c["clip_id"] == args.clip)
    trial = next(t for t in clip["asr_trials"] if t["chunk_ms"] == 10000)
    source = []
    for chunk in trial["chunks"]:
        raw = (Path(args.bundle) / chunk["path"]).read_bytes()
        source.append(raw[44:])
    src = np.frombuffer(b"".join(source), dtype="<i2").astype(np.float32)

    def envelope(x):
        hop = 160  # 10 ms at 16 kHz
        n = len(x) // hop
        e = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-6)
        e = np.log(e)
        return (e - e.mean()) / (e.std() + 1e-9)

    ce, se = envelope(cap), envelope(src)
    windows = []
    win = 500  # 5 s windows of 10 ms frames
    for start in range(0, len(ce) - win, args.step_frames):
        w = ce[start : start + win]
        if w.std() < 1e-3:
            continue
        corr = np.correlate(se, w, mode="valid") / win
        best = int(corr.argmax())
        windows.append({"capture_ms": start * 10, "media_ms": best * 10, "peak": float(corr[best]),
                        "offset_ms": best * 10 - start * 10})
    good = [w for w in windows if w["peak"] >= args.min_peak]
    fit = None
    if len(good) >= 3:
        x = np.array([w["capture_ms"] for w in good], dtype=float)
        y = np.array([w["media_ms"] for w in good], dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        residual = y - (slope * x + intercept)
        fit = {"media_ms_per_capture_ms": float(slope), "offset_ms": float(intercept),
               "residual_abs_ms": distribution([round(abs(r)) for r in residual]),
               "windows_used": len(good)}
    report = {
        "clip_id": args.clip,
        "method": "10 ms log-RMS envelope cross-correlation, 5 s capture windows against the "
        "bundle source PCM; linear fit of media time on capture time over windows above the "
        "peak threshold. Resolution 10 ms.",
        "min_peak": args.min_peak,
        "capture_samples": len(cap),
        "windows": windows,
        "fit": fit,
    }
    write_new(args.out, report)
    print(json.dumps({"windows": len(windows), "good": len(good), "fit": fit}, indent=2))
    return 0


# ---- device metadata and resources -------------------------------------------------------


def adb(adb_path, *args, check=True):
    result = subprocess.run([adb_path, *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def device_info(args):
    """Model/OS/ABI/CPU only. Never reads serials or account identifiers."""
    props = {}
    for key in ("ro.product.model", "ro.product.manufacturer", "ro.build.version.release",
                "ro.build.version.sdk", "ro.product.cpu.abi", "ro.board.platform",
                "ro.build.version.security_patch"):
        props[key] = adb(args.adb, "shell", "getprop", key).strip()
    cpu = adb(args.adb, "shell", "cat", "/proc/cpuinfo")
    features = sorted({f for line in cpu.splitlines() if line.startswith("Features")
                       for f in line.split(":", 1)[1].split()})
    parts = sorted({line.split(":", 1)[1].strip() for line in cpu.splitlines()
                    if line.startswith("CPU part")})
    mem = adb(args.adb, "shell", "cat", "/proc/meminfo").splitlines()[:3]
    info = {"properties": props, "cpu_features": features, "cpu_parts": parts,
            "cpu_count": cpu.count("processor\t:"), "meminfo": mem}
    write_new(args.out, info)
    print(json.dumps(info, indent=2))
    return 0


BATTERY_KEYS = ("AC powered", "USB powered", "Wireless powered", "status", "level", "scale",
                "temperature", "voltage", "health")


def parse_battery(text):
    values = {}
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        if key in BATTERY_KEYS:
            values[key] = value.strip()
    return values


def parse_thermal(text):
    status = re.search(r"Thermal Status:\s*(\d+)", text)
    temps = re.findall(r"Temperature\{mValue=([-\d.]+), mType=(\d+), mName=([^,]+), mStatus=(\d+)\}", text)
    return {
        "status": int(status.group(1)) if status else None,
        "temperatures": [{"value_c": float(v), "type": int(t), "name": n, "status": int(s)}
                         for v, t, n, s in temps],
    }


def sample_resources(args):
    """Poll battery and thermal state over adb (USB or Wi-Fi) into a JSONL log."""
    import time

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            battery = parse_battery(adb(args.adb, "shell", "dumpsys", "battery"))
            thermal = parse_thermal(adb(args.adb, "shell", "dumpsys", "thermalservice"))
            current = adb(args.adb, "shell", "cat", "/sys/class/power_supply/battery/current_now",
                          check=False).strip()
            row = {"t_s": round(elapsed, 3), "label": args.label, "battery": battery,
                   "thermal": thermal, "current_now": current or None}
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            if elapsed >= args.duration_s:
                break
            time.sleep(max(0.0, args.interval_s - (time.monotonic() - start - elapsed)))
    return 0


def summarize_resources(args):
    rows = [json.loads(line) for line in Path(args.log).read_text(encoding="utf-8").splitlines() if line]
    require(rows, args.log, "empty resource log")
    first, last = rows[0], rows[-1]

    def level(row):
        b = row["battery"]
        return round(int(b["level"]) * 1000 / int(b["scale"])) if b.get("level") else None

    def battery_temp(row):
        value = row["battery"].get("temperature")
        return int(value) * 100 if value else None  # tenths of a degree -> millicelsius

    def plugged(row):
        b = row["battery"]
        return any(b.get(k) == "true" for k in ("AC powered", "USB powered", "Wireless powered"))

    statuses = sorted({r["thermal"]["status"] for r in rows if r["thermal"]["status"] is not None})
    sensors = {}
    for r in rows:
        for t in r["thermal"]["temperatures"]:
            sensors.setdefault(t["name"], []).append(t["value_c"])
    currents = [int(r["current_now"]) for r in rows if r.get("current_now") and
                re.fullmatch(r"-?\d+", r["current_now"])]
    summary = {
        "label": first["label"],
        "elapsed_s": last["t_s"] - first["t_s"],
        "samples": len(rows),
        "battery_start_permille": level(first),
        "battery_end_permille": level(last),
        "battery_resolution": "integer percent from dumpsys battery (10 permille steps)",
        "battery_temperature_start_millicelsius": battery_temp(first),
        "battery_temperature_end_millicelsius": battery_temp(last),
        "battery_temperature_max_millicelsius": max((battery_temp(r) or -10**9) for r in rows),
        "plugged_any_sample": any(plugged(r) for r in rows),
        "unplugged_all_samples": not any(plugged(r) for r in rows),
        "thermal_statuses_seen": statuses,
        "thermal_sensor_max_c": {k: max(v) for k, v in sensors.items()},
        "current_now_samples": len(currents),
        "current_now_mean": statistics.fmean(currents) if currents else None,
        "current_now_note": "Raw /sys current_now; unit and sign are device-specific "
        "(reported, not converted).",
    }
    if args.out:
        write_new(args.out, summary)
    print(json.dumps(summary, indent=2))
    return 0


# ---- OCR replay -----------------------------------------------------------------------


def ocr_items(bundle):
    """(device file name, group, frame id, bundle path, sha256, width, height) for every image."""
    index = bundle_index(bundle)
    items = []
    for shot in index["ocr"]["screenshots"]:
        items.append(("screenshot", shot["id"], shot["path"], shot["sha256"], shot["width"], shot["height"]))
    for clip in index["ocr"]["source_clips"]:
        for frame in clip["frames"]:
            items.append((clip["clip_id"], frame["id"], frame["path"], frame["sha256"], frame["width"],
                          frame["height"]))
    rows = []
    for group, frame_id, path, sha, width, height in items:
        name = f"{group}__{frame_id}{Path(path).suffix}"
        rows.append({"file": name, "group": group, "frame_id": frame_id, "path": path, "sha256": sha,
                     "width": width, "height": height})
    return rows


def ocr_stage(args):
    """Copy the exact image bytes (no re-encode) into one flat directory for adb push."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    rows = ocr_items(args.bundle)
    for row in rows:
        data = (Path(args.bundle) / row["path"]).read_bytes()
        require(hashlib.sha256(data).hexdigest() == row["sha256"], row["path"], "hash mismatch")
        (out / row["file"]).write_bytes(data)
    write_new(Path(args.mapping), {"images": rows})
    print(f"{len(rows)} images staged")
    return 0


def read_ocr_run(path):
    """Replay JSONL: optional init row, then one row per image attempt."""
    init, rows = None, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") == "init":
            init = row
        elif row.get("type", "image") == "image":
            rows.append(row)
    return init, rows


def ocr_words(row, width, height):
    """Element-level words with pixel boxes. Boxes reaching past the image are intersected
    with it for scoring and counted, never silently dropped."""
    words, outside = [], 0
    for line in row.get("lines", []):
        for element in line.get("elements") or [line]:
            text = (element.get("text") or "").strip()
            box = element.get("box")
            if not text or not box or not tokens(text, VERSION):
                continue
            left, top, right, bottom = box
            if left < 0 or top < 0 or right > width or bottom > height:
                outside += 1
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right <= left or bottom <= top:
                outside += 1
                continue
            words.append({"text": text, "box": [left, top, right, bottom],
                          "confidence": element.get("confidence")})
    return words, outside


def check_identity(row, image):
    require(row.get("sha256") == image["sha256"], row.get("file", "?"), "image bytes differ from bundle")
    require((row.get("width"), row.get("height")) == (image["width"], image["height"]),
            row.get("file", "?"), "decoded dimensions differ from bundle")


def ocr_score(args):
    from ocr_experiment import normalized_box, selected_region_errors

    bundle = Path(args.bundle)
    index = bundle_index(bundle)
    images = {r["file"]: r for r in ocr_items(bundle)}
    init, rows = read_ocr_run(args.run)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    by_file = {}
    for row in rows:
        if row.get("mode", args.mode) == args.mode:
            by_file.setdefault(row["file"], []).append(row)
    rows = [r for rows_ in by_file.values() for r in rows_]
    failures = [r for r in rows if r.get("status") != "ok"]
    outside_total = 0

    # Screenshots: accuracy scored once (repetition 0); latency over every repetition.
    shots, shot_walls = [], []
    counts = {"substitution": 0, "deletion": 0, "insertion": 0, "reference_tokens": 0}
    unmatched = 0
    for shot in index["ocr"]["screenshots"]:
        name = f"screenshot__{shot['id']}{Path(shot['path']).suffix}"
        attempts = sorted(by_file.get(name, []), key=lambda r: r.get("rep", 0))
        require(attempts, name, "screenshot not attempted")
        for attempt in attempts:
            check_identity(attempt, images[name])
            shot_walls.append(round(attempt["wall_ms"]))
        first = attempts[0]
        words, outside = ocr_words(first, shot["width"], shot["height"]) if first["status"] == "ok" else ([], 0)
        outside_total += outside
        result = selected_region_errors(shot["regions"], words, shot["width"], shot["height"])
        for key in counts:
            counts[key] += result["word_errors"][key]
        unmatched += result["unmatched_words"]
        shots.append({"id": shot["id"], "status": first["status"], "repetitions": len(attempts),
                      "word_errors": result["word_errors"], "unmatched_words": result["unmatched_words"],
                      "regions": result["regions"], "boxes_outside_image": outside})
    counts["wer"] = (counts["substitution"] + counts["deletion"] + counts["insertion"]) / counts[
        "reference_tokens"] if counts["reference_tokens"] else None
    write_new(out / "screenshots.json", {"shots": shots})

    # Source frames: same inputs as the Tesseract source-frame runs, ML Kit detections.
    clips = []
    template_dir = bundle / "evidence" / "res02-local-ocr"
    for clip in index["ocr"]["source_clips"]:
        template = read_json(template_dir / f"{clip['clip_id']}-tesseract-fast.input.json")
        frames = []
        for frame in clip["frames"]:
            name = f"{clip['clip_id']}__{frame['id']}{Path(frame['path']).suffix}"
            attempts = by_file.get(name, [])
            require(attempts, name, "frame not attempted")
            row = attempts[0]
            check_identity(row, images[name])
            ok = row["status"] == "ok"
            words, outside = ocr_words(row, frame["width"], frame["height"]) if ok else ([], 0)
            outside_total += outside
            frames.append({
                "id": frame["id"], "presentation_ms": frame["presentation_ms"], "sha256": frame["sha256"],
                "wall_ms": round(row["wall_ms"]), "status": "ok" if ok else "OCR_UNAVAILABLE",
                "detections": [{"text": w["text"], "box": normalized_box(w["box"], frame["width"],
                                                                          frame["height"])} for w in words],
            })
        data = dict(template)
        data.update(
            run_id=f"{clip['clip_id']}-{args.label}", model="ml-kit/latin-v2",
            model_revision=args.model_revision, hardware=args.hardware,
            provenance=f"{args.provenance}; replay output SHA256 {sha256_file(args.run)}",
            limitations=[
                "Source-video workstation frames replayed on the phone, not device-captured frames.",
                "Full-image ML Kit element boxes; no production text-region crop.",
                "No full-screen reference: deduplication and preservation only, not a missed-text rate.",
            ],
            frames=frames,
        )
        write_new(out / f"{clip['clip_id']}-{args.label}.input.json", data)
        result = score(data, Path(args.corpus))
        write_new(out / f"{clip['clip_id']}-{args.label}.score.json", result)
        clips.append({"clip_id": clip["clip_id"], "frames": len(frames),
                      "failed_frames": result["metrics"]["failed_frames"],
                      "wall_ms_per_frame": result["metrics"]["wall_ms_per_frame"],
                      "detections": sum(len(f["detections"]) for f in frames),
                      "deduplicated_tracks": len(result["metrics"]["deduplicated_tracks"])})
    all_walls = [round(r["wall_ms"]) for r in rows if not r["file"].startswith("screenshot__")]
    summary = {
        "model": "ml-kit/latin-v2", "model_revision": args.model_revision, "hardware": args.hardware,
        "mode": args.mode,
        "replay_output_sha256": sha256_file(args.run), "init": init,
        "attempts": len(rows), "failed_attempts": len(failures),
        "boxes_outside_image": outside_total,
        "screenshots": {"images": len(shots), "selected_region_word_errors": counts,
                        "unmatched_words": unmatched, "wall_ms": distribution(shot_walls)},
        "source_frames": {"clips": clips, "wall_ms": distribution(all_walls)},
    }
    write_new(out / "summary.json", summary)
    print(json.dumps(summary, indent=2)[:3000])
    return 0


CARD_SCREENSHOTS = {
    # Supplied card screenshot -> (clip, adjudicated on-screen-text window).
    "card-0": ("social-01", 18000, 20000),
    "card-1": ("social-05", 20000, 23000),
    "card-2": ("social-05", 23000, 30000),
    "card-3": ("social-05", 73000, 80000),
    "card-4": ("social-05", 155000, 158000),
    "card-5": ("social-05", 163000, 165000),
}


def card_windows(corpus):
    """On-screen-text windows from the corpus-local adjudications (as in the #92 audit)."""
    windows = []
    for line in (Path(corpus) / "adjudications.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        intervals = sorted({(d["start_ms"], d["end_ms"]) for d in row["decisions"]
                            if d["modality"] == "on-screen-text"})
        for start, end in intervals:
            windows.append({"clip_id": row["clip_id"], "id": f"window-{start}-{end}",
                            "start_ms": start, "end_ms": end})
    return windows


def token_recall(reference_tokens, observed_tokens):
    """Fraction of reference tokens present in the observed multiset (order-free)."""
    pool = {}
    for token in observed_tokens:
        pool[token] = pool.get(token, 0) + 1
    found = 0
    for token in reference_tokens:
        if pool.get(token, 0) > 0:
            pool[token] -= 1
            found += 1
    return found / len(reference_tokens) if reference_tokens else None


def read_video_run(path):
    clips, probes = {}, {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["type"] == "clip":
            clips[row["file"]] = row
        elif row["type"] == "probe":
            probes.setdefault(row["file"], []).append(row)
    return clips, probes


def ocr_video_report(args):
    """Fixed 5 s versus shipped change-triggered sampling on decoded eval media."""
    bundle = Path(args.bundle)
    index = bundle_index(bundle)
    shots = {s["id"]: s for s in index["ocr"]["screenshots"]}
    windows = card_windows(args.corpus)
    clips, probes = read_video_run(args.run)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    report = {"run_sha256": sha256_file(args.run), "clips": [], "windows": []}
    kept_names = ("changed", "heartbeat")
    for clip in index["clips"]:
        clip_id = clip["clip_id"]
        name = next(n for n in clips if Path(n).stem == clip_id)
        meta = clips[name]
        require(meta["sha256"] == clip["media"]["sha256"], name, "media bytes differ from bundle")
        rows = probes[name]
        decisions = {}
        for row in rows:
            decisions[row.get("decision", row.get("status"))] = decisions.get(
                row.get("decision", row.get("status")), 0) + 1
        kept = [r for r in rows if r.get("decision") in kept_names]
        fixed = [r for r in rows if r.get("fixed_tick")]
        per_minute = max((sum(1 for k in kept if m <= k["probe_ms"] < m + 60000)
                          for m in range(0, meta["duration_ms"], 1000)), default=0)
        reads = [r["recognition_ms"] for r in rows if "recognition_ms" in r]
        report["clips"].append({
            "clip_id": clip_id, "duration_ms": meta["duration_ms"], "probes": len(rows),
            "decisions": decisions, "kept_frames": len(kept), "fixed_frames": len(fixed),
            "max_kept_in_any_60s": per_minute,
            "frame_unavailable": sum(1 for r in rows if r.get("status") == "frame_unavailable"),
            "failed_frames": sum(1 for r in rows if r.get("frame_status") == "failed"),
            "no_text_region_frames": sum(1 for r in rows if r.get("frame_status") == "no_text_regions"),
            "recognition_ms": distribution([round(x) for x in reads]),
            "decode_ms": distribution([round(r["decode_ms"]) for r in rows]),
        })
        write_new(out / f"{clip_id}-ocr-observations.input.json",
                  dedup_input(clip, rows, kept, fixed, args))
        result = score(read_json(out / f"{clip_id}-ocr-observations.input.json"), Path(args.corpus))
        report["clips"][-1]["deduplicated_tracks"] = len(result["metrics"]["deduplicated_tracks"])
        report["clips"][-1]["observations"] = sum(
            len(o["observations"]) for o in result["metrics"]["deduplicated_tracks"])
        report["clips"][-1]["input_observations"] = sum(
            len(r.get("lines", [])) for r in rows if r in kept or r in fixed)
        for window in (w for w in windows if w["clip_id"] == clip_id):
            report["windows"].append(window_result(window, rows, kept, fixed, shots))
    report["summary"] = window_summary(report["windows"])
    report["limitations"] = [
        "Frames decoded from the original media with MediaMetadataRetriever at 1 Hz probe times "
        "and scaled to the capture long edge; not screen captures through MediaProjection.",
        "Card windows are the corpus-local adjudicated on-screen-text intervals (AI-assisted, "
        "provisional); card text is the AI-assisted selected screenshot regions; recall is "
        "order-free token recall against those regions, not full-screen OCR ground truth.",
        "Screenshot and video geometry differ, so card reading is text-only (no box matching).",
    ]
    write_new(out / "report.json", report)
    print(json.dumps({"clips": report["clips"], "summary": report["summary"]}, indent=2)[:6000])
    return 0


def dedup_input(clip, rows, kept, fixed, args):
    selected = sorted({r["probe_ms"] for r in kept} | {r["probe_ms"] for r in fixed})
    by_ms = {r["probe_ms"]: r for r in rows}
    frames = []
    for ms in selected:
        row = by_ms[ms]
        detections = [
            {"text": line["text"], "box": line["box_normalized"]}
            for line in row.get("lines", [])
            if tokens(line["text"], VERSION) and line["box_normalized"][0] < line["box_normalized"][2]
            and line["box_normalized"][1] < line["box_normalized"][3]
        ]
        frames.append({
            "id": f"frame-{ms}", "presentation_ms": ms,
            "sha256": hashlib.sha256(f"{clip['media']['sha256']}:{ms}".encode()).hexdigest(),
            "wall_ms": round(row.get("recognition_ms", 0)),
            "status": "OCR_UNAVAILABLE" if row.get("frame_status") == "failed" else "ok",
            "detections": [] if row.get("frame_status") == "failed" else detections,
        })
    return {
        "schema_version": VERSION, "kind": "local-measurement", "task": "ocr",
        "run_id": f"{clip['clip_id']}-mlkit-shipped-sampler", "clip_id": clip["clip_id"],
        "corpus_manifest_sha256": bundle_index(args.bundle)["corpus_manifest_sha256"],
        "duration_ms": clip["duration_ms"], "timebase": "media", "model": "ml-kit/latin-v2",
        "model_revision": args.model_revision, "hardware": args.hardware,
        "provenance": f"{args.provenance}; frame sha256 values are synthetic identifiers "
        "(media sha256 + probe ms) because decoded bitmaps are not files",
        "limitations": ["Selected regions only; no full-screen reference.",
                        "Production text-region crop and line-level boxes."],
        "resources": None, "hosted_processing_approval": None,
        "reference_basis": "selected-regions", "iou_threshold_permille": 500, "reference": [],
        "frames": frames,
        "fixed_frame_ids": [f"frame-{r['probe_ms']}" for r in fixed],
        "fixed_tolerance_ms": 0,
        "change_triggered_frame_ids": [f"frame-{r['probe_ms']}" for r in kept],
    }


def window_result(window, rows, kept, fixed, shots):
    card = next((c for c, (clip, s, e) in CARD_SCREENSHOTS.items()
                 if clip == window["clip_id"] and s == window["start_ms"] and e == window["end_ms"]), None)
    reference = []
    if card is not None:
        reference = [t for region in shots[card]["regions"] for t in tokens(region["text"], VERSION)]

    def policy(selected):
        inside = [r for r in selected if window["start_ms"] <= r["probe_ms"] < window["end_ms"]]
        recalls = []
        for row in inside:
            observed = [t for line in row.get("lines", []) for t in tokens(line["text"], VERSION)]
            recalls.append(token_recall(reference, observed) if reference else None)
        best = max((r for r in recalls if r is not None), default=None)
        return {"frames_in_window": len(inside), "temporally_missed": not inside,
                "best_token_recall": best,
                "read_at_half_or_more": best is not None and best >= 0.5}

    return {"clip_id": window["clip_id"], "window": window["id"],
            "duration_ms": window["end_ms"] - window["start_ms"], "card_screenshot": card,
            "reference_tokens": len(reference), "fixed_5s": policy(fixed),
            "change_triggered": policy(kept)}


def window_summary(results):
    summary = {}
    for key in ("fixed_5s", "change_triggered"):
        scored = [r for r in results if r["reference_tokens"]]
        summary[key] = {
            "windows": len(results),
            "temporally_missed": sum(r[key]["temporally_missed"] for r in results),
            "windows_with_card_text": len(scored),
            "card_text_read_at_half_or_more": sum(r[key]["read_at_half_or_more"] for r in scored),
            "brief_windows_le_3s": sum(1 for r in results if r["duration_ms"] <= 3000),
            "brief_windows_temporally_missed": sum(
                r[key]["temporally_missed"] for r in results if r["duration_ms"] <= 3000),
        }
    return summary


def tesseract_run(args):
    """Pinned Tesseract on exact image bytes, emitting the replay JSONL format.

    Settings follow the RES-02 protocol: LSTM only, sparse text, English,
    OMP_THREAD_LIMIT=1, native word boxes and confidence, init timed separately.
    Requires tesserocr and Pillow (optional, not repository dependencies).
    """
    import os
    import time

    os.environ["OMP_THREAD_LIMIT"] = "1"
    import tesserocr
    from PIL import Image

    from ocr_experiment import recognize

    files = sorted(p for p in Path(args.images).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    out = Path(args.out)
    with out.open("x", encoding="utf-8", newline="\n") as stream:
        started = time.perf_counter()
        api = tesserocr.PyTessBaseAPI(path=args.tessdata, lang="eng", psm=tesserocr.PSM.SPARSE_TEXT,
                                      oem=tesserocr.OEM.LSTM_ONLY)
        api.SetVariable("classify_enable_learning", "0")
        init_ms = (time.perf_counter() - started) * 1000
        stream.write(json.dumps({"type": "init", "engine": tesserocr.tesseract_version(),
                                 "tesserocr": tesserocr.__version__, "tessdata": args.tessdata,
                                 "model_sha256": sha256_file(Path(args.tessdata) / "eng.traineddata"),
                                 "init_ms": init_ms}) + "\n")
        for rep in range(args.reps):
            for path in files:
                data = path.read_bytes()
                try:
                    with Image.open(path) as image:
                        image.load()
                        width, height = image.size
                        result = recognize(api, image)
                    row = {"status": "ok", "wall_ms": result["wall_ms"],
                           "lines": [{"text": w["text"], "box": w["box"], "confidence": w["confidence"]}
                                     for w in result["words"]]}
                except (OSError, Invalid, RuntimeError) as error:
                    width = height = None
                    row = {"status": "OCR_UNAVAILABLE", "wall_ms": 0, "error": type(error).__name__}
                stream.write(json.dumps({"type": "image", "file": path.name, "rep": rep,
                                         "sha256": hashlib.sha256(data).hexdigest(), "width": width,
                                         "height": height, **row}) + "\n")
        api.End()
    return 0


def quota_ledger(args):
    """Reconcile real three-minute chunk output with the existing hosted request ledger.

    Measured: device chunk count, durations and PCM bytes (from `verify-capture`) and the
    #92 clip-d 0..180000 ms request prefixes. Verified headers: only the daily request
    limit and remaining-request counts observed in #92. Assumptions: dated public-plan
    values carried in the bundle's limits provenance. No new hosted call is made.
    """
    bundle = Path(args.bundle) / "evidence"
    timing = read_json(bundle / "res02-asr-timing-quota.json")
    results = read_json(bundle / "res02-results.json")
    template = read_json(
        bundle / "res02-selected-entity-scores" / "clip-d-10000-whisper-large-v3-selected-entities.input.json"
    )
    limits = template["limits"]
    capture = read_json(args.capture_report) if args.capture_report else None
    device = None
    if capture is not None:
        chunks = [c for c in capture["chunks"] if c["present"]]
        audio_ms = [c["end_ms"] - c["start_ms"] for c in chunks]
        wav_bytes = [c["pcm_bytes"] + 44 for c in chunks]
        billed = [max(ms, limits["minimum_audio_ms"]) for ms in audio_ms]
        device = {
            "source": "verify-capture report of a real AN-04 capture (measured)",
            "capture_duration_ms": capture["duration_ms"],
            "requests": len(chunks),
            "submitted_audio_ms": sum(audio_ms),
            "pcm_bytes_total": sum(c["pcm_bytes"] for c in chunks),
            "wav_bytes_if_wrapped_total": sum(wav_bytes),
            "max_wav_bytes_if_wrapped": max(wav_bytes) if wav_bytes else None,
            "max_zip_bytes": capture["max_chunk_bytes"],
            "within_file_cap": all(b <= limits["file_cap_bytes"] for b in wav_bytes),
            "estimated_minimum_billed_audio_ms": sum(billed),
            "short_final_chunk_ms": audio_ms[-1] if audio_ms and audio_ms[-1] < args.chunk_ms else None,
        }
        device["estimated_hourly_audio_fraction"] = sum(billed) / limits["audio_ms_per_hour"]
        device["daily_request_fraction"] = len(chunks) / limits["requests_per_day"]
        device["three_minute_captures_per_hour_by_audio"] = (
            limits["audio_ms_per_hour"] // max(1, sum(billed))
        )
        device["three_minute_captures_per_day_by_requests"] = limits["requests_per_day"] // max(1, len(chunks))
    ledger = {
        "evidence_classes": {
            "measured": "request counts, submitted audio, bytes and wall time from stored #92 attempts "
            "and real AN-04 chunk files",
            "verified_headers": "x-ratelimit-limit-requests and x-ratelimit-remaining-requests in #92 "
            "responses (dated 2026-10-05)",
            "assumptions": limits["provenance"],
            "estimates": "minimum-billed audio and hourly/daily fractions computed from the assumptions",
            "unavailable": "billed audio seconds, account entitlement and shared-account traffic are "
            "not exposed by the observed headers; no billing dashboard evidence was supplied",
        },
        "assumed_limits": limits,
        "hosted_three_minute_prefixes": timing["three_minute_prefixes"],
        "hosted_full_run": {
            "requests": sum(results["statuses"].values()),
            "statuses": results["statuses"],
            "actual_audio_ms_submitted": results["actual_audio_ms_submitted"],
            "estimated_minimum_billed_audio_ms": results["estimated_minimum_billed_audio_ms"],
            "observed_daily_request_limit_headers": results["observed_daily_request_limit_headers"],
        },
        "device_capture_projection": device,
        "new_hosted_calls": 0,
    }
    write_new(args.out, ledger)
    print(json.dumps({"device_capture_projection": device, "assumed_limits": limits}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    c = commands.add_parser("asr-list", help="write the 142-chunk replay list")
    c.add_argument("--bundle", required=True)
    c.add_argument("--device-dir", required=True)
    c.add_argument("--out", required=True)

    c = commands.add_parser("asr-score", help="convert one driver run and score it")
    c.add_argument("--bundle", required=True)
    c.add_argument("--corpus", default=str(ROOT / "corpus-local"))
    c.add_argument("--run", required=True)
    c.add_argument("--model", required=True)
    c.add_argument("--model-revision", required=True)
    c.add_argument("--hardware", required=True)
    c.add_argument("--provenance", required=True)
    c.add_argument("--label", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--allow-missing", action="store_true")

    c = commands.add_parser("groq-baseline", help="aggregate existing Groq scores")
    c.add_argument("--bundle", required=True)
    c.add_argument("--out", required=True)

    c = commands.add_parser("verify-capture", help="verify a pulled AN-04 capture directory")
    c.add_argument("--capture", required=True)
    c.add_argument("--chunk-ms", type=int, default=10000)
    c.add_argument("--upload-cap", type=int, default=256 * 1024 * 1024)
    c.add_argument("--in-progress", action="store_true",
                   help="verify the sealed chunks of a snapshot taken while recording")
    c.add_argument("--out", required=True)

    c = commands.add_parser("align-capture", help="estimate capture-to-media mapping from audio")
    c.add_argument("--capture", required=True)
    c.add_argument("--bundle", required=True)
    c.add_argument("--clip", required=True)
    c.add_argument("--step-frames", type=int, default=250)
    c.add_argument("--min-peak", type=float, default=0.6)
    c.add_argument("--out", required=True)

    c = commands.add_parser("device-info", help="record model/OS/ABI without identifiers")
    c.add_argument("--adb", required=True)
    c.add_argument("--out", required=True)

    c = commands.add_parser("sample-resources", help="poll battery/thermal into JSONL")
    c.add_argument("--adb", required=True)
    c.add_argument("--label", required=True)
    c.add_argument("--duration-s", type=float, default=180)
    c.add_argument("--interval-s", type=float, default=10)
    c.add_argument("--out", required=True)

    c = commands.add_parser("summarize-resources", help="summarize one resource log")
    c.add_argument("--log", required=True)
    c.add_argument("--out")

    c = commands.add_parser("ocr-stage", help="stage exact image bytes for phone replay")
    c.add_argument("--bundle", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--mapping", required=True)

    c = commands.add_parser("ocr-score", help="score an ML Kit replay against the bundle")
    c.add_argument("--bundle", required=True)
    c.add_argument("--corpus", default=str(ROOT / "corpus-local"))
    c.add_argument("--run", required=True)
    c.add_argument("--model-revision", required=True)
    c.add_argument("--hardware", required=True)
    c.add_argument("--provenance", required=True)
    c.add_argument("--label", required=True)
    c.add_argument("--mode", default="full-image", choices=("full-image", "production-crop"))
    c.add_argument("--out", required=True)

    c = commands.add_parser("ocr-video-report", help="fixed vs shipped change-triggered sampling")
    c.add_argument("--bundle", required=True)
    c.add_argument("--corpus", default=str(ROOT / "corpus-local"))
    c.add_argument("--run", required=True)
    c.add_argument("--model-revision", required=True)
    c.add_argument("--hardware", required=True)
    c.add_argument("--provenance", required=True)
    c.add_argument("--out", required=True)

    c = commands.add_parser("tesseract-run", help="pinned Tesseract on a directory of images")
    c.add_argument("--images", required=True)
    c.add_argument("--tessdata", required=True)
    c.add_argument("--reps", type=int, default=1)
    c.add_argument("--out", required=True)

    c = commands.add_parser("quota-ledger", help="reconcile real chunks with the hosted ledger")
    c.add_argument("--bundle", required=True)
    c.add_argument("--capture-report")
    c.add_argument("--chunk-ms", type=int, default=10000)
    c.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "asr-list":
            Path(args.out).write_text(asr_list(args.bundle, args.device_dir), encoding="utf-8",
                                      newline="\n")
            return 0
        return {
            "asr-score": asr_score,
            "groq-baseline": groq_baseline,
            "verify-capture": verify_capture,
            "align-capture": align_capture,
            "device-info": device_info,
            "sample-resources": sample_resources,
            "summarize-resources": summarize_resources,
            "quota-ledger": quota_ledger,
            "ocr-stage": ocr_stage,
            "ocr-score": ocr_score,
            "tesseract-run": tesseract_run,
            "ocr-video-report": ocr_video_report,
        }[args.command](args)
    except (Invalid, OSError, KeyError, ValueError, RuntimeError) as error:
        print(f"RES-02b failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
