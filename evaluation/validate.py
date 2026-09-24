"""Validate the repository's small, explicit JSON Schema subset and dataset links."""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
TABLES = {
    "clips": ("clip", "clip_id"),
    "annotations": ("annotation", "annotation_id"),
    "adjudications": ("adjudication", "adjudication_id"),
}
KEYWORDS = {
    "$schema", "title", "description", "type", "const", "enum", "required",
    "properties", "additionalProperties", "items", "minItems", "maxItems",
    "uniqueItems", "minLength", "pattern", "minimum", "maximum",
}
TYPES = {"object", "array", "string", "integer", "boolean", "null"}


class Invalid(ValueError):
    pass


def require(condition, location, message):
    if not condition:
        raise Invalid(f"{location}: {message}")


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, key, "duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise Invalid(f"non-finite JSON number: {value}")


def parse(text, location):
    try:
        return json.loads(text, object_pairs_hook=object_pairs,
                          parse_constant=reject_constant)
    except (ValueError, RecursionError) as error:
        raise Invalid(f"{location}: {error}") from error


def read_json(path):
    return parse(path.read_text(encoding="utf-8"), str(path))


def check_schema(schema, location="$schema"):
    """Fail closed on schema features this validator does not implement."""
    require(isinstance(schema, dict), location, "expected a schema object")
    require(not (schema.keys() - KEYWORDS), location,
            f"unsupported keywords: {sorted(schema.keys() - KEYWORDS)}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        require(bool(types) and all(isinstance(t, str) and t in TYPES for t in types),
                location, "unsupported type")
    for key in ("minItems", "maxItems", "minLength"):
        if key in schema:
            require(type(schema[key]) is int and schema[key] >= 0, location,
                    f"{key} must be a nonnegative integer")
    for key in ("minimum", "maximum"):
        if key in schema:
            require(type(schema[key]) is int or
                    (type(schema[key]) is float and math.isfinite(schema[key])),
                    location, f"{key} must be finite")
    if "additionalProperties" in schema:
        require(type(schema["additionalProperties"]) is bool, location,
                "additionalProperties must be boolean")
    if "uniqueItems" in schema:
        require(type(schema["uniqueItems"]) is bool, location, "uniqueItems must be boolean")
    if "enum" in schema:
        require(isinstance(schema["enum"], list) and bool(schema["enum"]), location,
                "enum must be a nonempty array")
    if "required" in schema:
        require(isinstance(schema["required"], list)
                and all(isinstance(k, str) for k in schema["required"]), location,
                "required must be an array of property names")
    if "pattern" in schema:
        require(isinstance(schema["pattern"], str), location, "pattern must be a string")
        try:
            re.compile(schema["pattern"])
        except re.error as error:
            raise Invalid(f"{location}: invalid pattern: {error}") from error
    properties = schema.get("properties", {})
    require(isinstance(properties, dict), location, "properties must be an object")
    for name, child in properties.items():
        check_schema(child, f"{location}.properties.{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{location}.items")


def json_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def validate_value(value, schema, location):
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int or (
            type(value) is float and math.isfinite(value) and value.is_integer()),
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        require(any(matches[t] for t in types), location, f"expected {schema['type']}")
    if "const" in schema:
        require(json_equal(value, schema["const"]), location, f"expected {schema['const']!r}")
    if "enum" in schema:
        require(any(json_equal(value, v) for v in schema["enum"]), location, "not in enum")
    if isinstance(value, dict):
        require(not (set(schema.get("required", [])) - value.keys()), location,
                f"missing required fields: {sorted(set(schema.get('required', [])) - value.keys())}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            require(not (value.keys() - properties.keys()), location,
                    f"unknown fields: {sorted(value.keys() - properties.keys())}")
        for name, item in value.items():
            if name in properties:
                validate_value(item, properties[name], f"{location}.{name}")
    if isinstance(value, list):
        require(len(value) >= schema.get("minItems", 0), location, "too few items")
        require(len(value) <= schema.get("maxItems", len(value)), location, "too many items")
        if schema.get("uniqueItems"):
            require(not any(json_equal(item, prior) for i, item in enumerate(value)
                            for prior in value[:i]), location, "duplicate array item")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_value(item, schema["items"], f"{location}[{index}]")
    if isinstance(value, str):
        require(len(value) >= schema.get("minLength", 0), location, "string too short")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, location, "pattern mismatch")
    if type(value) in (int, float):
        require(type(value) is int or math.isfinite(value), location, "number must be finite")
        require(value >= schema.get("minimum", value), location, "below minimum")
        require(value <= schema.get("maximum", value), location, "above maximum")


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def load_rows(path, schema, id_key):
    rows = {}
    locations = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        location = f"{path}:{line_number}"
        require(bool(line.strip()), location, "blank JSONL row")
        row = parse(line, location)
        validate_value(row, schema, location)
        record_id = row[id_key]
        location += f" [{record_id}]"
        require(record_id not in rows, location, "duplicate record ID")
        rows[record_id] = row
        locations[record_id] = location
    return rows, locations


def check_occurrence(row, duration, location):
    require(0 <= row["start_ms"] < row["end_ms"] <= duration, location,
            "interval must satisfy 0 <= start_ms < end_ms <= clip duration")
    factual = row["reason"] in {"factual-claim", "factual-premise"}
    require(row["eligible"] == factual, location, "eligibility and reason disagree")
    require(bool(row["proposition"].strip()), location, "proposition must not be whitespace")


def check_media_path(value, location):
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(p not in {"", ".", ".."} for p in value.split("/"))
            and "\\" not in value and ":" not in value, location,
            "media path must be a portable relative path without traversal")
    return path


def validate_dataset(directory, *, frozen=False, media_root=None):
    schemas = {}
    for name in ("dataset", "clip", "annotation", "adjudication"):
        schemas[name] = read_json(ROOT / "schemas" / f"{name}.schema.json")
        check_schema(schemas[name], f"{name}.schema.json")
    manifest = read_json(directory / "dataset.json")
    validate_value(manifest, schemas["dataset"], str(directory / "dataset.json"))
    require(manifest["kind"] == ("frozen" if frozen else "examples"), directory,
            "dataset kind does not match requested mode")
    data, locations = {}, {}
    for table, (schema_name, id_key) in TABLES.items():
        path = directory / f"{table}.jsonl"
        require(digest(path) == manifest["files"][path.name], path, "snapshot SHA-256 mismatch")
        data[table], locations[table] = load_rows(path, schemas[schema_name], id_key)
    clips = data["clips"]
    require(bool(clips), directory, "dataset has no clips")
    if frozen:
        require(10 <= len(clips) <= 20, directory, "frozen dataset requires 10-20 clips")
    require({c["split"] for c in clips.values()} == {"dev", "test"}, directory,
            "both dev and test splits must be nonempty")
    groups = {}
    coverage = set()
    media_paths = set()
    for clip_id, clip in clips.items():
        loc = locations["clips"][clip_id]
        require(clip["synthetic"] is not frozen, loc, "synthetic status does not match dataset kind")
        require(clip["duration_ms"] <= (180000 if clip["style"] == "live" else 600000),
                loc + ".duration_ms", "clip exceeds style duration limit")
        require((clip["rights"]["basis"] == "synthetic") is not frozen, loc + ".rights",
                "rights basis does not match dataset kind")
        for field in ("reference", "attribution"):
            require(bool(clip["rights"][field].strip()), loc + ".rights." + field,
                    "rights evidence must not be whitespace")
        for field in ("creator_ids", "topic_ids", "repost_group_id"):
            values = [clip[field]] if field == "repost_group_id" else clip[field]
            for group in values:
                key = (field, group)
                require(key not in groups or groups[key] == clip["split"], loc + "." + field,
                        f"split leakage for {group}")
                groups[key] = clip["split"]
        media = clip["media"]
        if frozen:
            require(clip["source_url"] is not None, loc + ".source_url", "real source URL required")
            require(media["path"] is not None and media["sha256"] is not None, loc + ".media",
                    "real media path and SHA-256 required")
        else:
            require(clip["source_url"] is None and media["path"] is None and media["sha256"] is None,
                    loc, "examples must not reference real sources or media")
        if media["path"] is not None:
            relative = check_media_path(media["path"], loc + ".media.path")
            require(media["path"] not in media_paths, loc + ".media.path", "duplicate media path")
            media_paths.add(media["path"])
            key = ("sha256", media["sha256"])
            require(key not in groups or groups[key] == clip["split"], loc + ".media.sha256",
                    "identical media appears across splits")
            groups[key] = clip["split"]
            if media_root is not None:
                root = media_root.resolve()
                path = root.joinpath(*relative.parts).resolve()
                require(path.is_relative_to(root), loc + ".media.path", "media escapes root")
                require(digest(path) == media["sha256"], loc + ".media.sha256", "media SHA-256 mismatch")
        coverage.update(clip["coverage"])
    if frozen:
        required = set(schemas["clip"]["properties"]["coverage"]["items"]["enum"])
        require(required <= coverage, directory, f"missing coverage: {sorted(required - coverage)}")

    passes = {clip_id: {} for clip_id in clips}
    for annotation_id, annotation in data["annotations"].items():
        loc = locations["annotations"][annotation_id]
        clip_id = annotation["clip_id"]
        require(clip_id in clips, loc + ".clip_id", "unknown clip")
        require(annotation["synthetic"] == clips[clip_id]["synthetic"], loc, "synthetic status mismatch")
        occurrences = {}
        for index, occurrence in enumerate(annotation["occurrences"]):
            occurrence_loc = f"{loc}.occurrences[{index}]"
            require(occurrence["occurrence_id"] not in occurrences, occurrence_loc, "duplicate occurrence ID")
            check_occurrence(occurrence, clips[clip_id]["duration_ms"], occurrence_loc)
            occurrences[occurrence["occurrence_id"]] = occurrence
        passes[clip_id][annotation_id] = occurrences
    for clip_id, reviews in passes.items():
        loc = locations["clips"][clip_id]
        require(len(reviews) == 2, loc, "exactly two whole-clip annotation passes required")
        require(len({data["annotations"][a]["annotator_id"] for a in reviews}) == 2,
                loc, "two distinct annotators required")

    adjudicated = set()
    for adjudication_id, adjudication in data["adjudications"].items():
        loc = locations["adjudications"][adjudication_id]
        clip_id = adjudication["clip_id"]
        require(clip_id in clips, loc + ".clip_id", "unknown clip")
        require(clip_id not in adjudicated, loc, "duplicate clip adjudication")
        adjudicated.add(clip_id)
        require(adjudication["synthetic"] == clips[clip_id]["synthetic"], loc, "synthetic status mismatch")
        require(set(adjudication["annotation_ids"]) == set(passes[clip_id]), loc + ".annotation_ids",
                "must reference both passes for this clip")
        require(bool(adjudication["review_note"].strip()), loc, "review note must not be whitespace")
        expected = {(a, o) for a, occurrences in passes[clip_id].items() for o in occurrences}
        used, gold_ids, propositions = set(), set(), {}
        for index, decision in enumerate(adjudication["decisions"]):
            decision_loc = f"{loc}.decisions[{index}]"
            require(decision["gold_id"] not in gold_ids, decision_loc, "duplicate gold ID")
            gold_ids.add(decision["gold_id"])
            check_occurrence(decision, clips[clip_id]["duration_ms"], decision_loc)
            require(bool(decision["resolution"].strip()), decision_loc, "resolution must not be whitespace")
            proposition_id = decision["proposition_id"]
            require(proposition_id not in propositions
                    or propositions[proposition_id] == decision["proposition"], decision_loc,
                    "one proposition ID must have one normalized text")
            propositions[proposition_id] = decision["proposition"]
            for reference in decision["references"]:
                key = (reference["annotation_id"], reference["occurrence_id"])
                require(key in expected, decision_loc + ".references", "unknown occurrence reference")
                used.add(key)
        require(used == expected, loc + ".decisions", "every original occurrence needs an adjudication reference")
        has_claims = any(d["eligible"] for d in adjudication["decisions"])
        require(("no-assessable-claims" in clips[clip_id]["coverage"]) == (not has_claims), loc,
                "no-assessable-claims tag must match adjudicated gold")
    require(adjudicated == set(clips), directory, "every clip requires adjudication")
    return len(clips)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=ROOT / "examples")
    parser.add_argument("--frozen", action="store_true", help="require the real 10-20-clip corpus")
    parser.add_argument("--media-root", type=Path, help="also verify local media bytes (never uploaded)")
    args = parser.parse_args(argv)
    try:
        count = validate_dataset(args.directory, frozen=args.frozen, media_root=args.media_root)
    except (Invalid, OSError, UnicodeError, RecursionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.frozen:
        print(f"Valid frozen metadata: {count} clips. "
              + ("Media hashes verified." if args.media_root else "Media bytes NOT verified; supply --media-root.")
              + " Human rights/review evidence still requires sign-off.")
    else:
        print(f"Valid synthetic examples: {count} clips. NOT a reviewed evaluation set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
