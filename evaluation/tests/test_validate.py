import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate import (
    Invalid, ROOT, TABLES, check_schema, digest, load_rows, main, parse, read_json,
    validate_dataset, validate_value,
)


class SchemaTest(unittest.TestCase):
    def test_all_id_schemas_reject_trailing_whitespace(self):
        def id_schemas(schema):
            for name, child in schema.get("properties", {}).items():
                if name.endswith("_id"):
                    yield name, child
                elif name.endswith("_ids"):
                    yield name, child["items"]
                yield from id_schemas(child)
            if "items" in schema:
                yield from id_schemas(schema["items"])

        checked = 0
        for path in (ROOT / "schemas").glob("*.json"):
            for name, schema in id_schemas(read_json(path)):
                checked += 1
                validate_value("valid-id-1", schema, name)
                for suffix in (" ", "\t", "\n", "\r", "\r\n", "\u0085", "\u2028", "\u2029", "\u00a0"):
                    with self.subTest(path=path.name, field=name, suffix=repr(suffix)):
                        with self.assertRaisesRegex(Invalid, "pattern mismatch"):
                            validate_value("valid-id-1" + suffix, schema, name)
        self.assertEqual(16, checked)

    def test_sha256_schemas_reject_trailing_whitespace(self):
        clip = read_json(ROOT / "schemas" / "clip.schema.json")
        dataset = read_json(ROOT / "schemas" / "dataset.schema.json")
        schemas = {
            "media.sha256": clip["properties"]["media"]["properties"]["sha256"],
            **dataset["properties"]["files"]["properties"],
        }
        self.assertEqual(4, len(schemas))
        for name, schema in schemas.items():
            validate_value("a" * 64, schema, name)
            for suffix in (" ", "\t", "\n", "\r", "\r\n", "\u0085", "\u2028", "\u2029", "\u00a0"):
                with self.subTest(field=name, suffix=repr(suffix)):
                    with self.assertRaisesRegex(Invalid, "pattern mismatch"):
                        validate_value("a" * 64 + suffix, schema, name)

    def test_schema_contracts_are_supported(self):
        for path in (ROOT / "schemas").glob("*.json"):
            with self.subTest(path=path):
                check_schema(read_json(path))

    def test_unknown_schema_keywords_and_malformed_schemas_fail(self):
        for schema in [
            {"format": "date"}, {"properties": {"child": {"$ref": "missing"}}},
            {"type": "unsupported"}, {"type": []}, {"items": False},
            {"required": "id"}, {"properties": []}, {"pattern": "["},
            {"pattern": 1}, {"minLength": -1}, {"maxItems": True},
            {"additionalProperties": {}}, {"uniqueItems": 1}, {"enum": []},
            {"minimum": "zero"}, {"maximum": float("inf")},
        ]:
            with self.subTest(schema=schema), self.assertRaises(Invalid):
                check_schema(schema)

    def test_types_constants_and_bounds(self):
        for value, schema in [
            (True, {"const": 1}), (1, {"const": True}), (False, {"enum": [0]}),
            (True, {"type": "integer"}), (1.5, {"type": "integer"}),
            (float("inf"), {"type": "integer"}), ("en", {"type": "array"}),
            ({}, {"required": ["id"]}), ({"extra": 1}, {"additionalProperties": False}),
            ([], {"minItems": 1}), ([1, 2], {"maxItems": 1}),
            ([{"a": 1, "b": 2}, {"b": 2, "a": 1}], {"uniqueItems": True}),
            ("", {"minLength": 1}), ("INVALID", {"pattern": "^[a-z]+$"}),
            (0, {"minimum": 1}), (3, {"maximum": 2}),
        ]:
            with self.subTest(value=value, schema=schema), self.assertRaises(Invalid):
                validate_value(value, schema, "test")
        validate_value(1.0, {"type": "integer"}, "test")
        validate_value(None, {"type": ["string", "null"], "pattern": "^x$"}, "test")
        validate_value([True, 1], {"uniqueItems": True}, "test")
        with self.assertRaisesRegex(Invalid, "maximum"):
            validate_value(10 ** 400, {"type": "integer", "maximum": 600000}, "test")

    def test_bad_json_has_location(self):
        for text in ['{"id":1,"id":2}', '{"a":NaN}', '{"a":Infinity}', "{"]:
            with self.subTest(text=text), self.assertRaisesRegex(Invalid, "fixture:3"):
                parse(text, "fixture:3")


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manifest = read_json(ROOT / "examples" / "dataset.json")
        self.rows = {
            table: [json.loads(line) for line in
                    (ROOT / "examples" / f"{table}.jsonl").read_text(encoding="utf-8").splitlines()]
            for table in TABLES
        }
        self.write()

    def write(self):
        for table, rows in self.rows.items():
            path = self.directory / f"{table}.jsonl"
            path.write_bytes(("".join(json.dumps(row) + "\n" for row in rows)).encode("utf-8"))
        self.rehash()

    def rehash(self):
        self.manifest["files"] = {
            f"{table}.jsonl": digest(self.directory / f"{table}.jsonl") for table in TABLES
        }
        (self.directory / "dataset.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def check_invalid(self, message, frozen=False):
        self.write()
        with self.assertRaisesRegex(Invalid, message):
            validate_dataset(self.directory, frozen=frozen)

    def frozen_fixture(self, count=10):
        """Fabricated temporary metadata tests mechanics, never human/rights approval."""
        originals = copy.deepcopy(self.rows)
        self.rows = {table: [] for table in TABLES}
        self.manifest["kind"] = "frozen"
        all_tags = read_json(ROOT / "schemas" / "clip.schema.json")["properties"]["coverage"]["items"]["enum"]
        for index in range(count):
            source_index = 1 if index == count - 1 else 0
            clip = copy.deepcopy(originals["clips"][source_index])
            old_id = clip["clip_id"]
            clip_id = f"clip-{index}"
            clip.update(clip_id=clip_id, synthetic=False,
                        source_url="https://example.invalid/temporary-test-only",
                        creator_ids=[f"creator-{index}"], topic_ids=[f"topic-{index}"],
                        repost_group_id=f"repost-{index}", split="dev" if index % 2 == 0 else "test")
            clip["rights"] = {"basis": "license", "reference": "FAKE temporary test evidence",
                              "attribution": "Test fixture only", "allows_redistribution": False}
            content = f"not media; temporary hash fixture {index}".encode()
            media_path = self.directory / "media" / f"{clip_id}.bin"
            media_path.parent.mkdir(exist_ok=True)
            media_path.write_bytes(content)
            clip["media"] = {"path": media_path.name, "sha256": hashlib.sha256(content).hexdigest()}
            if index == 0:
                clip["coverage"] = [tag for tag in all_tags if tag != "no-assessable-claims"]
            self.rows["clips"].append(clip)
            for row in originals["annotations"]:
                if row["clip_id"] == old_id:
                    annotation = copy.deepcopy(row)
                    annotation.update(clip_id=clip_id, synthetic=False,
                                      annotation_id=row["annotation_id"].replace(old_id, clip_id))
                    self.rows["annotations"].append(annotation)
            adjudication = copy.deepcopy(originals["adjudications"][source_index])
            adjudication.update(clip_id=clip_id, synthetic=False, adjudication_id=f"gold-{index}")
            adjudication["annotation_ids"] = [a.replace(old_id, clip_id) for a in adjudication["annotation_ids"]]
            for decision in adjudication["decisions"]:
                for reference in decision["references"]:
                    reference["annotation_id"] = reference["annotation_id"].replace(old_id, clip_id)
            self.rows["adjudications"].append(adjudication)
        self.write()

    def test_committed_examples_and_empty_passes(self):
        self.assertEqual(2, validate_dataset(ROOT / "examples"))

    def test_example_mode_cannot_pass_as_frozen(self):
        with self.assertRaisesRegex(Invalid, "kind"):
            validate_dataset(self.directory, frozen=True)

    def test_frozen_metadata_and_bytes_at_count_limits(self):
        for count in (10, 20):
            with self.subTest(count=count):
                self.setUp()
                self.frozen_fixture(count)
                self.assertEqual(count, validate_dataset(self.directory, frozen=True))
                self.assertEqual(count, validate_dataset(
                    self.directory, frozen=True, media_root=self.directory / "media"))

    def test_frozen_count_outside_limits(self):
        for count in (9, 21):
            with self.subTest(count=count):
                self.setUp()
                self.frozen_fixture(count)
                self.check_invalid("10-20", frozen=True)

    def test_hash_tampering(self):
        with (self.directory / "clips.jsonl").open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(Invalid, "SHA-256"):
            validate_dataset(self.directory)

    def test_empty_corpus_does_not_pass(self):
        self.rows = {table: [] for table in TABLES}
        self.check_invalid("no clips")

    def test_bad_jsonl_rejected_even_with_updated_hash(self):
        for text in ["\n", "{\n", "[]\n", '{"schema_version":1,"schema_version":1}\n']:
            with self.subTest(text=text):
                (self.directory / "clips.jsonl").write_text(text, encoding="utf-8")
                self.rehash()
                with self.assertRaisesRegex(Invalid, r"clips.jsonl:1"):
                    validate_dataset(self.directory)

    def test_missing_files_report_errors(self):
        (self.directory / "annotations.jsonl").unlink()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(1, main([str(self.directory)]))
        self.assertIn("annotations.jsonl", error.getvalue())

    def test_unicode_separators_are_preserved_inside_json_strings(self):
        text = "Before\u0085next\u2028line\u2029paragraph"
        self.rows["clips"][0]["rights"]["reference"] = text
        self.rows["adjudications"][0]["review_note"] = text
        for table, rows in self.rows.items():
            path = self.directory / f"{table}.jsonl"
            path.write_bytes("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                    for row in rows).encode("utf-8"))
        self.rehash()
        self.assertEqual(2, validate_dataset(self.directory))
        path = self.directory / "clips.jsonl"
        schema = read_json(ROOT / "schemas" / "clip.schema.json")
        rows, locations = load_rows(path, schema, "clip_id")
        self.assertEqual(text, rows["example-a"]["rights"]["reference"])
        self.assertEqual(f"{path}:2 [example-b]", locations["example-b"])

    def test_jsonl_reports_physical_line_numbers(self):
        schema = read_json(ROOT / "schemas" / "clip.schema.json")
        row = copy.deepcopy(self.rows["clips"][0])
        row["rights"]["reference"] = "First\u2028second\u2029third"
        path = self.directory / "clips.jsonl"
        first_line = json.dumps(row, ensure_ascii=False).encode("utf-8")
        for ending in (b"\n", b"\r\n"):
            for bad_row in (b"\n", b"{\n"):
                with self.subTest(ending=ending, bad_row=bad_row):
                    path.write_bytes(first_line + ending + bad_row)
                    with self.assertRaisesRegex(Invalid, r"clips.jsonl:2:"):
                        load_rows(path, schema, "clip_id")
        path.write_bytes(first_line)
        rows, _ = load_rows(path, schema, "clip_id")
        self.assertEqual(row, rows["example-a"])

    def test_schema_fields_and_duplicate_ids(self):
        for change, message in [
            (lambda c: c.update(language="fr"), "language"),
            (lambda c: c.update(device_id="private"), "unknown fields"),
            (lambda c: c.update(duration_ms=True), "integer"),
            (lambda c: c.update(duration_ms=0), "minimum"),
            (lambda c: c.update(synthetic=False), "synthetic status"),
        ]:
            with self.subTest(message=message):
                self.setUp()
                change(self.rows["clips"][0])
                self.check_invalid(message)
        self.setUp()
        self.rows["clips"].append(copy.deepcopy(self.rows["clips"][0]))
        self.check_invalid("duplicate record")

    def test_duration_boundaries(self):
        self.rows["clips"][0]["duration_ms"] = 180000
        self.rows["clips"][1]["duration_ms"] = 600000
        self.write()
        self.assertEqual(2, validate_dataset(self.directory))
        self.rows["clips"][0]["duration_ms"] = 180001
        self.check_invalid("style duration")
        self.rows["clips"][0]["duration_ms"] = 180000
        self.rows["clips"][1]["duration_ms"] = 600001
        self.check_invalid("maximum")

    def test_group_leakage_and_single_split(self):
        for field in ("creator_ids", "topic_ids", "repost_group_id"):
            with self.subTest(field=field):
                self.setUp()
                self.rows["clips"][1][field] = copy.deepcopy(self.rows["clips"][0][field])
                self.check_invalid("split leakage")
        self.setUp()
        self.rows["clips"][1]["split"] = "dev"
        self.check_invalid("nonempty")

    def test_annotation_coverage_and_identity(self):
        self.rows["annotations"].pop()
        self.check_invalid("exactly two")
        self.setUp()
        self.rows["annotations"][1]["annotator_id"] = self.rows["annotations"][0]["annotator_id"]
        self.check_invalid("distinct annotators")
        self.setUp()
        self.rows["annotations"][0]["blind_to_model_output"] = False
        self.check_invalid("blind_to_model_output")
        self.setUp()
        self.rows["annotations"][0]["independent"] = False
        self.check_invalid("independent")
        self.setUp()
        self.rows["annotations"][0]["clip_id"] = "unknown"
        self.check_invalid("unknown clip")

    def test_trailing_newline_cannot_bypass_annotator_distinctness(self):
        self.rows["annotations"][1]["annotator_id"] = (
            self.rows["annotations"][0]["annotator_id"] + "\n")
        self.check_invalid("annotator_id: pattern mismatch")

    def test_trailing_newline_cannot_bypass_split_isolation(self):
        for field in ("creator_ids", "topic_ids", "repost_group_id"):
            with self.subTest(field=field):
                self.setUp()
                source = self.rows["clips"][0][field]
                self.rows["clips"][1][field] = (
                    source + "\n" if isinstance(source, str) else [source[0] + "\n"])
                self.check_invalid("pattern mismatch")

    def test_occurrence_and_gold_boundaries(self):
        for table, field in (("annotations", "occurrences"), ("adjudications", "decisions")):
            for start, end in ((4000, 3000), (1000, 1000), (0, 12001), (-1, 2000)):
                with self.subTest(table=table, start=start, end=end):
                    self.setUp()
                    self.rows[table][0][field][0].update(start_ms=start, end_ms=end)
                    self.check_invalid("interval|minimum")
        self.setUp()
        self.rows["annotations"][0]["occurrences"].append(
            copy.deepcopy(self.rows["annotations"][0]["occurrences"][0]))
        self.check_invalid("duplicate occurrence")

    def test_eligibility_and_gold_references(self):
        self.rows["annotations"][0]["occurrences"][0]["eligible"] = False
        self.check_invalid("eligibility")
        self.setUp()
        self.rows["adjudications"][0]["annotation_ids"][1] = "other-pass"
        self.check_invalid("both passes")
        self.setUp()
        self.rows["adjudications"][0]["decisions"][0]["references"][0]["occurrence_id"] = "unknown"
        self.check_invalid("unknown occurrence")
        self.setUp()
        self.rows["adjudications"][0]["decisions"].pop()
        self.check_invalid("every original occurrence")
        self.setUp()
        self.rows["adjudications"].pop()
        self.check_invalid("every clip")

    def test_duplicate_adjudication_and_empty_resolution(self):
        duplicate = copy.deepcopy(self.rows["adjudications"][0])
        duplicate["adjudication_id"] = "another-gold"
        self.rows["adjudications"].append(duplicate)
        self.check_invalid("duplicate clip adjudication")
        self.setUp()
        self.rows["adjudications"][0]["decisions"][0]["resolution"] = " "
        self.check_invalid("resolution")

    def test_no_claims_tag_and_proposition_identity(self):
        self.rows["clips"][1]["coverage"] = []
        self.check_invalid("no-assessable-claims")
        self.setUp()
        decisions = self.rows["adjudications"][0]["decisions"]
        decisions[1]["proposition_id"] = decisions[0]["proposition_id"]
        self.check_invalid("normalized text")

    def test_adjudicator_discoveries_and_splits_preserve_originals(self):
        decisions = self.rows["adjudications"][0]["decisions"]
        discovered = copy.deepcopy(decisions[0])
        discovered.update(gold_id="discovered", references=[], resolution="Adjudicator discovery.")
        decisions.append(discovered)
        split = copy.deepcopy(decisions[0])
        split.update(gold_id="split", resolution="Split original into another occurrence.")
        decisions.append(split)
        self.write()
        self.assertEqual(2, validate_dataset(self.directory))

    def test_frozen_coverage_and_provenance(self):
        for change, message in [
            (lambda c: c.update(coverage=[]), "missing coverage"),
            (lambda c: c.update(synthetic=True), "synthetic status"),
            (lambda c: c.update(source_url=None), "source URL"),
            (lambda c: c["rights"].update(basis="synthetic"), "rights basis"),
            (lambda c: c["rights"].update(reference="   "), "whitespace"),
            (lambda c: c["media"].update(path=None), "media path"),
            (lambda c: c["media"].update(sha256=None), "SHA-256 required"),
        ]:
            with self.subTest(message=message):
                self.setUp()
                self.frozen_fixture()
                change(self.rows["clips"][0])
                self.check_invalid(message, frozen=True)

    def test_media_paths_and_cross_split_duplicates(self):
        for path in ("../outside", "/absolute", "C:/file", "a\\b", "./file", "a//b"):
            with self.subTest(path=path):
                self.setUp()
                self.frozen_fixture()
                self.rows["clips"][0]["media"]["path"] = path
                self.check_invalid("relative path", frozen=True)
        self.setUp()
        self.frozen_fixture()
        self.rows["clips"][1]["media"]["sha256"] = self.rows["clips"][0]["media"]["sha256"]
        self.check_invalid("identical media", frozen=True)

    def test_local_media_hash_verification(self):
        self.frozen_fixture()
        (self.directory / "media" / "clip-0.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(Invalid, "media SHA-256"):
            validate_dataset(self.directory, frozen=True, media_root=self.directory / "media")

    def test_trailing_newline_cannot_bypass_media_split_isolation(self):
        self.frozen_fixture()
        dev, test = self.rows["clips"][:2]
        self.assertEqual(("dev", "test"), (dev["split"], test["split"]))
        media_root = self.directory / "media"
        (media_root / test["media"]["path"]).write_bytes(
            (media_root / dev["media"]["path"]).read_bytes())
        test["media"]["sha256"] = dev["media"]["sha256"] + "\n"
        self.check_invalid(r"media\.sha256: pattern mismatch", frozen=True)

    def test_media_symlink_escape(self):
        self.frozen_fixture()
        path = self.directory / "media" / "clip-0.bin"
        path.unlink()
        try:
            path.symlink_to(self.directory / "clips.jsonl")
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        with self.assertRaisesRegex(Invalid, "escapes root"):
            validate_dataset(self.directory, frozen=True, media_root=self.directory / "media")

    def test_media_symlink_within_root_is_allowed(self):
        self.frozen_fixture()
        path = self.directory / "media" / "clip-0.bin"
        target = self.directory / "media" / "clip-0-target.bin"
        content = path.read_bytes()
        target.write_bytes(content)
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        self.assertEqual(
            10,
            validate_dataset(self.directory, frozen=True, media_root=self.directory / "media"),
        )

    def test_missing_media_entry_is_rejected(self):
        self.frozen_fixture()
        (self.directory / "media" / "clip-0.bin").unlink()
        with self.assertRaisesRegex(Invalid, "media path not found"):
            validate_dataset(self.directory, frozen=True, media_root=self.directory / "media")

    def test_directory_media_entry_is_rejected(self):
        self.frozen_fixture()
        path = self.directory / "media" / "clip-0.bin"
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(Invalid, "media path must reference a file"):
            validate_dataset(self.directory, frozen=True, media_root=self.directory / "media")

    def test_cli_disclosures_and_failure_exit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main([str(self.directory)]))
        self.assertIn("NOT a reviewed evaluation set", output.getvalue())
        self.frozen_fixture()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main([str(self.directory), "--frozen"]))
        self.assertIn("Media bytes NOT verified", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main([str(self.directory), "--frozen", "--media-root",
                                      str(self.directory / "media")]))
        self.assertIn("Media hashes verified", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(1, main([str(self.directory)]))
        self.assertIn("ERROR:", error.getvalue())

    def test_unsupported_schema_cannot_silently_pass(self):
        with patch("validate.read_json", return_value={"format": "unknown"}):
            with self.assertRaisesRegex(Invalid, "unsupported keywords"):
                validate_dataset(self.directory)


if __name__ == "__main__":
    unittest.main()
