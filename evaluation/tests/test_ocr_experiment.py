import sys
import tempfile
import types
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr_experiment import (
    changed,
    normalized_box,
    recognize,
    sample_video,
    selected_region_errors,
    window_coverage,
)
from validate import Invalid


class OcrExperimentTests(unittest.TestCase):
    def test_sampler_checks_probe_tail_not_just_fixed_count(self):
        image = MagicMock()
        image.size = (100, 100)
        image.width = image.height = 100
        image.convert.return_value = image
        image.resize.return_value = image
        image.tobytes.return_value = bytes(4096)
        image.save.side_effect = lambda output, **kwargs: output.write(b"invented-jpeg")
        container = MagicMock()
        container.__enter__.return_value = container
        container.streams.video = [object()]
        modules = {
            "av": types.SimpleNamespace(open=lambda path: container),
            "PIL": types.SimpleNamespace(
                Image=types.SimpleNamespace(Resampling=types.SimpleNamespace(LANCZOS=1, BILINEAR=2))
            ),
        }
        for seconds in (6, 10, 9):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as temporary:
                duration = 9125 if seconds == 9 else 10000
                container.decode.return_value = [
                    types.SimpleNamespace(
                        pts=t * 1000, time_base=Fraction(1, 1000), to_image=lambda: image
                    )
                    for t in range(seconds)
                ]
                if seconds == 9:
                    container.decode.return_value.append(
                        types.SimpleNamespace(
                            pts=8958, time_base=Fraction(1, 1000), to_image=lambda: image
                        )
                    )
                with patch.dict(sys.modules, modules):
                    output = Path(temporary) / "samples"
                    if seconds == 6:
                        with self.assertRaisesRegex(Invalid, "incomplete 1 Hz"):
                            sample_video(Path("invented"), duration, output)
                    else:
                        result = sample_video(Path("invented"), duration, output)
                        self.assertEqual(result["fixed_frame_ids"], ["frame-0", "frame-5000"])
                        self.assertEqual(
                            result["change_triggered_frame_ids"], result["fixed_frame_ids"]
                        )
                        self.assertEqual(
                            result["unavailable_final_probe_ms"], 9000 if seconds == 9 else None
                        )

    def test_textless_iterator_entry_is_not_a_recognition_failure(self):
        api = MagicMock()
        iterator = api.GetIterator.return_value
        iterator.Empty.side_effect = [True, False]
        iterator.Next.side_effect = [True, False]
        iterator.GetUTF8Text.return_value = "visible"
        iterator.BoundingBox.return_value = (1, 2, 30, 40)
        iterator.Confidence.return_value = 90
        with patch.dict(
            sys.modules, {"tesserocr": types.SimpleNamespace(RIL=types.SimpleNamespace(WORD=1))}
        ):
            result = recognize(api, object())
        self.assertEqual(len(result["words"]), 1)
        iterator.GetUTF8Text.assert_called_once()

    def test_coordinates_use_actual_dimensions(self):
        self.assertEqual(normalized_box([10, 20, 50, 60], 100, 200), [1000, 1000, 5000, 3000])
        with self.assertRaises(Invalid):
            normalized_box([10, 20, 101, 60], 100, 200)

    def test_word_centers_join_lines_without_penalizing_unannotated_text(self):
        regions = [{"id": "headline", "box": [0, 0, 100, 50], "text": "Not 10 kg"}]
        words = [
            {"box": [1, 1, 25, 10], "text": "Not"},
            {"box": [26, 1, 35, 10], "text": "10"},
            {"box": [1, 20, 20, 30], "text": "kg"},
            {"box": [1, 60, 50, 80], "text": "Unannotated"},
        ]
        result = selected_region_errors(regions, words, 100, 100)
        self.assertEqual(result["word_errors"]["wer"], 0)
        self.assertEqual(result["unmatched_words"], 1)

    def test_missing_detection_counts_as_deletions(self):
        result = selected_region_errors(
            [{"id": "missing", "box": [0, 0, 50, 50], "text": "not 10"}], [], 100, 100
        )
        self.assertEqual(result["word_errors"]["deletion"], 2)
        self.assertEqual(result["word_errors"]["wer"], 1)

    def test_overlapping_gold_cannot_double_count_words(self):
        with self.assertRaises(Invalid):
            selected_region_errors(
                [
                    {"id": "one", "box": [0, 0, 50, 50], "text": "one"},
                    {"id": "two", "box": [25, 25, 75, 75], "text": "two"},
                ],
                [],
                100,
                100,
            )

    def test_shared_boundary_belongs_to_only_one_region(self):
        result = selected_region_errors(
            [
                {"id": "left", "box": [0, 0, 50, 50], "text": "left"},
                {"id": "right", "box": [50, 0, 100, 50], "text": "right"},
            ],
            [{"box": [40, 10, 60, 20], "text": "right"}],
            100,
            100,
        )
        self.assertEqual(result["word_errors"]["deletion"], 1)
        self.assertEqual(result["word_errors"]["insertion"], 0)

    def test_trigger_threshold_and_size_checks(self):
        self.assertFalse(changed(bytes([0, 0]), bytes([11, 12])))
        self.assertTrue(changed(bytes([0, 0]), bytes([12, 12])))
        with self.assertRaises(Invalid):
            changed(b"", b"")

    def test_card_window_end_is_exclusive(self):
        windows = [{"id": "brief", "start_ms": 18000, "end_ms": 20000}]
        self.assertEqual(window_coverage(windows, [15000, 20000])["miss_rate"], 1)
        self.assertEqual(window_coverage(windows, [18000, 20000])["miss_rate"], 0)
        self.assertIsNone(window_coverage([], [])["miss_rate"])


if __name__ == "__main__":
    unittest.main()
