"""Explicitly authorized Groq trials using existing dev media and SRT references.

Preparation is offline. Only the separate run command can upload audio.
"""

import argparse
import io
import json
import math
import os
import platform
import re
import sys
import time
import wave
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from benchmark import (
    ASR_MODELS,
    HASH,
    ID,
    NOTE,
    VERSION,
    align,
    array,
    corpus,
    distribution,
    input_schema,
    integer,
    obj,
    ordered_segments,
    score,
    tokens,
)
from validate import ROOT, Invalid, digest, parse, read_json, require, validate_value

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELS = [name.removeprefix("groq/") for name in ASR_MODELS[:2]]
CHUNK_LENGTHS = (10000, 15000)
SPEC = obj(
    approval=NOTE,
    references=array(obj(clip_id=ID, path=NOTE, sha256=HASH), 1, 7),
    limits=input_schema("asr")["properties"]["limits"],
)
PLAN = obj(
    version={"const": "res02-groq-trial-v1"},
    created_at=NOTE,
    corpus=NOTE,
    corpus_manifest_sha256=HASH,
    specification_sha256=HASH,
    approval=NOTE,
    limits=SPEC["properties"]["limits"],
    models={"const": MODELS},
    endpoint={"const": ENDPOINT},
    runtime=NOTE,
    settings=obj(
        language={"const": "en"},
        temperature={"const": "0"},
        response_format={"const": "verbose_json"},
    ),
    maximum_requests=integer(1, 2000),
    observations=array(
        obj(
            clip_id=ID,
            duration_ms=integer(1, 600000),
            chunk_ms={"type": "integer", "enum": list(CHUNK_LENGTHS)},
            reference=obj(clip_id=ID, path=NOTE, sha256=HASH, cue_count=integer(1, 2000)),
            decoding=obj(
                zero_filled_samples=integer(),
                out_of_media_samples_removed=integer(),
                maximum_source_clock_quantization_samples=integer(),
                source_sample_rate=integer(1),
            ),
            source_media_sha256=HASH,
            chunks=array(
                obj(
                    seq=integer(),
                    start_ms=integer(),
                    end_ms=integer(1),
                    path=NOTE,
                    sha256=HASH,
                    size_bytes=integer(1),
                ),
                1,
                60,
            ),
        ),
        2,
        14,
    ),
)


def read_srt(path, duration_ms):
    """Retain reviewed wording and cue envelopes; never repair or infer timing."""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    timestamp = r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})"
    for number, block in enumerate(re.split(r"\n[ \t]*\n", text.strip()), 1):
        lines = block.split("\n")
        require(len(lines) >= 3 and lines[0] == str(number), "SRT", "invalid cue sequence")
        match = re.fullmatch(timestamp + r" --> " + timestamp, lines[1])
        require(match is not None, "SRT", f"invalid timestamps at cue {number}")
        values = [int(value) for value in match.groups()]
        bounds = [
            ((values[i] * 60 + values[i + 1]) * 60 + values[i + 2]) * 1000 + values[i + 3]
            for i in (0, 4)
        ]
        wording = "\n".join(lines[2:])
        require("<" not in wording and ">" not in wording, "SRT", "markup needs explicit review")
        cues.append({"start_ms": bounds[0], "end_ms": bounds[1], "text": wording})
    ordered_segments(cues, duration_ms, "SRT")
    require(sum(len(tokens(c["text"])) for c in cues) <= 5000, "SRT", "token ceiling exceeded")
    return cues


def subtitle_timing(reference, chunks):
    """Compare exact whole-cue text anchors; never treat cue bounds as word truth."""
    words = []
    starts, ends = {}, {}
    for cue in reference:
        starts[len(words)] = cue["start_ms"]
        words.extend(tokens(cue["text"]))
        ends[len(words)] = cue["end_ms"]
    hypothesis, segments, outside = [], [], []
    for chunk in chunks:
        for index, segment in enumerate(chunk["segments"]):
            start, end = segment["start"], segment["end"]
            require(
                type(start) in (int, float)
                and type(end) in (int, float)
                and math.isfinite(start)
                and math.isfinite(end)
                and start <= end,
                "provider timestamps",
                "non-finite, reversed or nonnumeric segment",
            )
            first = len(hypothesis)
            hypothesis.extend(tokens(segment["text"]))
            row = {
                "chunk_seq": chunk["seq"],
                "segment_index": index,
                "start_ms": round(start * 1000) + chunk["start_ms"],
                "end_ms": round(end * 1000) + chunk["start_ms"],
                "first_token": first,
                "end_token": len(hypothesis),
            }
            segments.append(row)
            if start < 0 or end * 1000 > chunk["end_ms"] - chunk["start_ms"]:
                outside.append(row)
    mapping = {j: i for op, i, j in align(words, hypothesis) if op == "equal"}
    matches = []
    for segment in segments:
        first, end = segment["first_token"], segment["end_token"]
        mapped = [mapping.get(j) for j in range(first, end)]
        if not mapped or any(i is None for i in mapped):
            continue
        left, right = mapped[0], mapped[-1] + 1
        if mapped != list(range(left, right)) or left not in starts or right not in ends:
            continue
        matches.append(
            {
                **segment,
                "reference_start_ms": starts[left],
                "reference_end_ms": ends[right],
                "start_delta_ms": segment["start_ms"] - starts[left],
                "end_delta_ms": segment["end_ms"] - ends[right],
            }
        )
    return {
        "basis": "exact text match to complete subtitle cue envelopes, possibly multiple cues",
        "provider_segments": len(segments),
        "matched_segments": len(matches),
        "unmatched_segments": len(segments) - len(matches),
        "start_signed_ms": distribution([m["start_delta_ms"] for m in matches]),
        "end_signed_ms": distribution([m["end_delta_ms"] for m in matches]),
        "absolute_endpoints_ms": distribution(
            [abs(m[key]) for m in matches for key in ("start_delta_ms", "end_delta_ms")]
        ),
        "outside_chunk_segments": outside,
        "matches": matches,
        "limitation": "Selected exact-text anchors, not all speech or independently verified "
        "word timing. "
        "Raw out-of-chunk times are flagged, never clipped or presented as valid capture offsets.",
    }


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def sample_clock(frame, expected):
    require(frame.pts is not None, "audio", "missing source presentation timestamp")
    actual = round(frame.pts * frame.time_base * frame.sample_rate)
    position = actual if expected is None else expected
    tolerance = math.ceil(frame.time_base * frame.sample_rate)
    require(abs(actual - position) <= tolerance, "audio", "source clock discontinuity")
    frame.pts = position
    frame.time_base = Fraction(1, frame.sample_rate)
    return position + frame.samples, abs(actual - position)


def pcm_audio(path, duration_ms):
    import av

    pcm = bytearray(duration_ms * 32)
    cursor = 0
    gaps = clipped = 0
    source_cursor = None
    maximum_clock_adjustment = 0
    with av.open(str(path)) as container:
        require(len(container.streams.audio) == 1, "audio", "expected exactly one audio track")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

        def consume(frame):
            nonlocal cursor, gaps, clipped
            require(frame.pts is not None, "audio", "missing presentation timestamp")
            position = round(frame.pts * frame.time_base * 16000)
            raw = bytes(frame.planes[0])[: frame.samples * 2]
            if position < 0:
                discard = min(-position, frame.samples)
                clipped += discard
                raw = raw[discard * 2 :]
                position += discard
            if not raw:
                return
            require(position >= cursor, "audio", "overlapping or nonmonotonic decoded audio")
            limit = len(pcm) // 2
            gaps += max(0, min(position, limit) - min(cursor, limit))
            count = min(len(raw) // 2, max(0, limit - position))
            clipped += len(raw) // 2 - count
            if count:
                pcm[position * 2 : (position + count) * 2] = raw[: count * 2]
            cursor = position + len(raw) // 2

        for decoded in container.decode(audio=0):
            # Millisecond container timestamps quantize a continuous sample clock.
            source_cursor, adjustment = sample_clock(decoded, source_cursor)
            maximum_clock_adjustment = max(maximum_clock_adjustment, adjustment)
            for frame in resampler.resample(decoded):
                consume(frame)
        for frame in resampler.resample(None):
            consume(frame)
    require(cursor > 0, "audio", "no decoded samples")
    gaps += max(0, len(pcm) // 2 - cursor)
    return bytes(pcm), {
        "zero_filled_samples": gaps,
        "out_of_media_samples_removed": clipped,
        "maximum_source_clock_quantization_samples": maximum_clock_adjustment,
        "source_sample_rate": decoded.sample_rate,
    }


def wav_bytes(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(pcm)
    return output.getvalue()


def prepare(directory, media_root, specification, output):
    import av

    spec = read_json(specification)
    validate_value(spec, SPEC, "specification")
    require(spec["limits"] is not None, "limits", "supply dated limits or disclosed assumptions")
    _, clips = corpus(directory, media_root)
    dev = {clip["clip_id"]: clip for clip in clips if clip["split"] == "dev"}
    selected = [row["clip_id"] for row in spec["references"]]
    require(len(set(selected)) == len(selected), "references", "duplicate clip")
    require(set(selected) <= dev.keys(), "references", "unknown or holdout clip")
    requests = sum(
        math.ceil(dev[clip_id]["duration_ms"] / length)
        for clip_id in selected
        for length in CHUNK_LENGTHS
    ) * len(MODELS)
    estimated_audio = sum(
        max(min(length, dev[clip_id]["duration_ms"] - start), spec["limits"]["minimum_audio_ms"])
        for clip_id in selected
        for length in CHUNK_LENGTHS
        for start in range(0, dev[clip_id]["duration_ms"], length)
    ) * len(MODELS)
    require(
        requests <= min(2000, spec["limits"]["requests_per_day"]),
        "budget",
        "trial exceeds declared daily request budget",
    )
    require(
        estimated_audio <= spec["limits"]["audio_ms_per_hour"],
        "budget",
        "trial exceeds declared hourly audio budget; schedule separately",
    )
    references = []
    for row in spec["references"]:
        path = Path(row["path"]).resolve()
        require(digest(path) == row["sha256"], "reference", "source hash mismatch")
        cues = read_srt(path, dev[row["clip_id"]]["duration_ms"])
        references.append({**row, "path": str(path), "cue_count": len(cues)})
    # Refuse an existing directory, so no previous artifacts can be overwritten.
    output.mkdir(parents=True, exist_ok=False)
    observations = []
    for reference in references:
        clip = dev[reference["clip_id"]]
        source = media_root / clip["media"]["path"]
        pcm, decoding = pcm_audio(source, clip["duration_ms"])
        for length in CHUNK_LENGTHS:
            chunks = []
            for seq, start in enumerate(range(0, clip["duration_ms"], length)):
                end = min(start + length, clip["duration_ms"])
                filename = f"{clip['clip_id']}-{length}-{seq:03}.wav"
                path = output / filename
                path.write_bytes(wav_bytes(pcm[start * 32 : end * 32]))
                require(
                    path.stat().st_size <= spec["limits"]["file_cap_bytes"], "WAV", "cap exceeded"
                )
                chunks.append(
                    {
                        "seq": seq,
                        "start_ms": start,
                        "end_ms": end,
                        "path": filename,
                        "sha256": digest(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
            observations.append(
                {
                    "clip_id": clip["clip_id"],
                    "duration_ms": clip["duration_ms"],
                    "chunk_ms": length,
                    "reference": reference,
                    "decoding": decoding,
                    "source_media_sha256": digest(source),
                    "chunks": chunks,
                }
            )
    plan = {
        "version": "res02-groq-trial-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "corpus": str(directory.resolve()),
        "corpus_manifest_sha256": digest(directory / "dataset.json"),
        "specification_sha256": digest(specification),
        "approval": spec["approval"],
        "limits": spec["limits"],
        "models": MODELS,
        "endpoint": ENDPOINT,
        "runtime": (
            f"Python {platform.python_version()}; PyAV {av.__version__}; {platform.system()}"
        ),
        "settings": {"language": "en", "temperature": "0", "response_format": "verbose_json"},
        "maximum_requests": sum(len(row["chunks"]) for row in observations) * len(MODELS),
        "observations": observations,
    }
    write_json(output / "plan.json", plan)
    return {
        "maximum_requests": plan["maximum_requests"],
        "plan_sha256": digest(output / "plan.json"),
    }


def checked_plan(path, expected_hash):
    require(digest(path) == expected_hash, "plan", "approval digest mismatch")
    plan = read_json(path)
    validate_value(plan, PLAN, "plan")
    require(plan["limits"] is not None, "limits", "missing dated limits")
    require(plan["version"] == "res02-groq-trial-v1", "plan", "unsupported version")
    require(
        plan["endpoint"] == ENDPOINT and plan["models"] == MODELS, "plan", "unexpected provider"
    )
    directory = Path(plan["corpus"])
    require(
        digest(directory / "dataset.json") == plan["corpus_manifest_sha256"], "corpus", "changed"
    )
    _, clips = corpus(directory)
    dev = {c["clip_id"]: c for c in clips if c["split"] == "dev"}
    pairs = set()
    for row in plan["observations"]:
        key = (row["clip_id"], row["chunk_ms"])
        require(
            key not in pairs and key[0] in dev and key[1] in CHUNK_LENGTHS, "plan", "invalid trial"
        )
        pairs.add(key)
        require(row["duration_ms"] == dev[key[0]]["duration_ms"], "plan", "duration mismatch")
        reference = row["reference"]
        require(reference["clip_id"] == key[0], "reference", "clip mismatch")
        require(digest(Path(reference["path"])) == reference["sha256"], "reference", "changed")
        require(
            len(read_srt(Path(reference["path"]), row["duration_ms"])) == reference["cue_count"],
            "reference",
            "cue count mismatch",
        )
        require(
            row["source_media_sha256"] == dev[key[0]]["media"]["sha256"],
            "media",
            "source does not match corpus",
        )
        end = 0
        for seq, chunk in enumerate(row["chunks"]):
            require(chunk["seq"] == seq and chunk["start_ms"] == end, "chunks", "noncontiguous")
            require(chunk["end_ms"] == min(end + key[1], row["duration_ms"]), "chunks", "bounds")
            end = chunk["end_ms"]
            file = (path.parent / chunk["path"]).resolve()
            require(file.parent == path.parent.resolve(), "chunk", "outside plan directory")
            require(digest(file) == chunk["sha256"], "chunk", "changed")
            require(
                file.stat().st_size == chunk["size_bytes"] <= plan["limits"]["file_cap_bytes"],
                "chunk",
                "byte cap or size mismatch",
            )
        require(end == row["duration_ms"], "chunks", "missing tail")
    require(
        pairs == {(clip, length) for clip, _ in pairs for length in CHUNK_LENGTHS},
        "plan",
        "both chunk lengths required for every selected clip",
    )
    require(
        plan["maximum_requests"] == sum(len(r["chunks"]) for r in plan["observations"]) * 2,
        "plan",
        "request budget mismatch",
    )
    return plan


def attempt_name(row, chunk, model):
    return f"{row['clip_id']}-{row['chunk_ms']}-{chunk['seq']:03}-{model}"


def run(path, expected_hash):
    import httpx

    plan = checked_plan(path, expected_hash)
    key = os.environ.get("GROQ_API_KEY")
    require(bool(key), "credential", "GROQ_API_KEY is not provisioned")
    attempts = path.parent / "attempts"
    attempts.mkdir(exist_ok=True)
    last_start = time.monotonic()
    completed = 0
    # No automatic retries or redirects. An interrupted start marker requires review.
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        for row in plan["observations"]:
            for chunk in row["chunks"]:
                for model in MODELS[:: -1 if chunk["seq"] % 2 else 1]:
                    name = attempt_name(row, chunk, model)
                    result = attempts / f"{name}.json"
                    marker = attempts / f"{name}.started.json"
                    if result.exists():
                        record = read_json(result)
                        require(record["plan_sha256"] == expected_hash, "attempt", "plan changed")
                        require(
                            record["status"] == 200,
                            "attempt",
                            "previous failure; no automatic retry",
                        )
                        completed += 1
                        continue
                    require(
                        not marker.exists(),
                        "attempt",
                        "interrupted request; review before resuming",
                    )
                    time.sleep(max(0, 3.1 - (time.monotonic() - last_start)))
                    write_json(
                        marker, {"plan_sha256": expected_hash, "at": datetime.now(UTC).isoformat()}
                    )
                    last_start = time.monotonic()
                    try:
                        response = client.post(
                            ENDPOINT,
                            headers={"Authorization": f"Bearer {key}"},
                            data={**plan["settings"], "model": model},
                            files={
                                "file": (
                                    "chunk.wav",
                                    (path.parent / chunk["path"]).read_bytes(),
                                    "audio/wav",
                                )
                            },
                        )
                    except httpx.RequestError as error:
                        write_json(
                            result,
                            {
                                "plan_sha256": expected_hash,
                                "status": None,
                                "error_type": type(error).__name__,
                                "wall_ms": round((time.monotonic() - last_start) * 1000),
                            },
                        )
                        raise Invalid(
                            "provider transport failed; recorded locally; no automatic retry"
                        ) from None
                    write_json(
                        result,
                        {
                            "plan_sha256": expected_hash,
                            "status": response.status_code,
                            "wall_ms": round((time.monotonic() - last_start) * 1000),
                            "rate_limit_headers": {
                                k: v
                                for k, v in response.headers.items()
                                if k.startswith("x-ratelimit-") or k in ("retry-after", "date")
                            },
                            "response": response.text,
                        },
                    )
                    require(
                        response.status_code == 200,
                        "provider",
                        f"HTTP {response.status_code}; stopped; inspect private attempt record",
                    )
                    completed += 1
            print(f"Completed {completed}/{plan['maximum_requests']} requests", flush=True)
    return {"completed_requests": completed, "status": "completed"}


def summarize(path, expected_hash):
    plan = checked_plan(path, expected_hash)
    results = []
    artifacts = []
    output = path.parent / "scores"
    require(not output.exists(), "scores", "output already exists; do not overwrite prior results")
    for row in plan["observations"]:
        for model in MODELS:
            chunks = []
            for chunk in row["chunks"]:
                record = read_json(
                    path.parent / "attempts" / f"{attempt_name(row, chunk, model)}.json"
                )
                require(
                    record["plan_sha256"] == expected_hash and record["status"] == 200,
                    "attempt",
                    "missing successful observation; partial run is not a full comparison",
                )
                payload = parse(record["response"], "provider response")
                require(
                    isinstance(payload, dict) and isinstance(payload.get("text"), str),
                    "response",
                    "missing transcription text",
                )
                text = payload["text"]
                chunks.append(
                    {
                        **{k: chunk[k] for k in ("seq", "start_ms", "end_ms", "size_bytes")},
                        "wall_ms": record["wall_ms"],
                        "status": "ok",
                        "segments": [
                            {
                                "start_ms": 0,
                                "end_ms": chunk["end_ms"] - chunk["start_ms"],
                                "text": text,
                            }
                        ]
                        if tokens(text)
                        else [],
                    }
                )
            data = {
                "schema_version": VERSION,
                "kind": "local-measurement",
                "task": "asr",
                "run_id": f"{row['clip_id']}-{row['chunk_ms']}-{model}",
                "clip_id": row["clip_id"],
                "corpus_manifest_sha256": plan["corpus_manifest_sha256"],
                "duration_ms": row["duration_ms"],
                "timebase": "media",
                "model": f"groq/{model}",
                "model_revision": f"{model}; hosted alias, immutable revision not exposed",
                "hardware": f"Hosted Groq; client {plan['runtime']}",
                "provenance": (
                    f"Plan SHA256 {expected_hash}; "
                    f"reference SRT SHA256 {row['reference']['sha256']}"
                ),
                "limitations": [
                    "Subtitle agreement only, not certified verbatim ASR ground truth.",
                    "Hypothesis segments are whole-chunk text envelopes, not model word timing; "
                    "raw provider timestamps retained separately.",
                    "No critical-entity labels or media-reviewed timestamp pairs; "
                    "these metrics are unmeasured.",
                    "Workstation-derived PCM, not Android chunks or "
                    "device latency/battery/thermal measurements.",
                    "One attempt per chunk, alternating model order; no retries. "
                    "Model aliases are not immutable revisions.",
                    "Quota estimates use supplied dated assumptions; "
                    "response headers retain partial account evidence only.",
                ],
                "resources": None,
                "hosted_processing_approval": plan["approval"],
                "reference_basis": "subtitle-reference",
                "timing_basis": "cue-envelope",
                "reference": read_srt(Path(row["reference"]["path"]), row["duration_ms"]),
                "entities": [],
                "timestamp_pairs": [],
                "limits": plan["limits"],
                "chunks": chunks,
            }
            result = score(data, Path(plan["corpus"]))
            artifacts.append((f"{data['run_id']}.input.json", data))
            artifacts.append((f"{data['run_id']}.score.json", result))
            results.append(
                {
                    "clip_id": row["clip_id"],
                    "model": model,
                    "chunk_ms": row["chunk_ms"],
                    "metrics": result["metrics"],
                }
            )
    totals = []
    for length in CHUNK_LENGTHS:
        for model in MODELS:
            selected = [r for r in results if r["chunk_ms"] == length and r["model"] == model]
            counts = {
                key: sum(r["metrics"]["word_errors"][key] for r in selected)
                for key in ("substitution", "deletion", "insertion", "reference_tokens")
            }
            counts["wer"] = (
                sum(counts[k] for k in ("substitution", "deletion", "insertion"))
                / counts["reference_tokens"]
            )
            rows = [r for r in plan["observations"] if r["chunk_ms"] == length]
            records = [
                read_json(path.parent / "attempts" / f"{attempt_name(r, c, model)}.json")
                for r in rows
                for c in r["chunks"]
            ]
            totals.append(
                {
                    "model": model,
                    "chunk_ms": length,
                    "word_errors": counts,
                    "wall_ms": distribution([r["wall_ms"] for r in records]),
                    "requests_per_three_minutes": math.ceil(180000 / length),
                    "estimated_audio_ms_per_three_minutes": math.ceil(180000 / length)
                    * max(length, plan["limits"]["minimum_audio_ms"]),
                }
            )
    summary = {
        "plan_sha256": expected_hash,
        "status": "completed",
        "comparisons": totals,
        "per_clip": results,
        "decision": "pending_critical_error_and_device_review",
    }
    output.mkdir(exist_ok=False)
    for filename, data in [*artifacts, ("summary.json", summary)]:
        if filename.endswith(".score.json"):
            data["input_sha256"] = digest(output / filename.replace(".score.json", ".input.json"))
        write_json(output / filename, data)
    return {"status": "completed", "comparisons": totals}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser(
        "prepare", help="offline; reuse existing references and media"
    )
    preparation.add_argument("--corpus", type=Path, default=ROOT / "corpus-local")
    preparation.add_argument("--media-root", type=Path, required=True)
    preparation.add_argument("--specification", type=Path, required=True)
    preparation.add_argument("--output", type=Path, required=True)
    for name in ("run", "summarize"):
        command = commands.add_parser(name)
        command.add_argument("plan", type=Path)
        command.add_argument("--approve-plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.corpus, args.media_root, args.specification, args.output)
        else:
            result = (run if args.command == "run" else summarize)(
                args.plan, args.approve_plan_sha256
            )
        print(json.dumps(result, indent=2, allow_nan=False))
    except (Invalid, OSError, UnicodeError, ValueError, ImportError) as error:
        print(f"RES-02 experiment failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
