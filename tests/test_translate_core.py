import unittest
from unittest import mock

from translate_vtt_zh_deepl_native import should_translate, translate_lines_native


class ShouldTranslateTests(unittest.TestCase):
    def test_filters_vtt_structure_lines(self):
        self.assertFalse(should_translate("WEBVTT"))
        self.assertFalse(should_translate("00:00:01.000 --> 00:00:02.000"))
        self.assertFalse(should_translate("12"))
        self.assertFalse(should_translate("NOTE this is note"))
        self.assertFalse(should_translate("   "))

    def test_keeps_regular_text(self):
        self.assertTrue(should_translate("Hello world"))


class TranslateFallbackTests(unittest.TestCase):
    @mock.patch("translate_vtt_zh_deepl_native.requests.post")
    def test_batch_failure_falls_back_and_reports(self, mock_post):
        mock_post.side_effect = RuntimeError("network down")
        errors = []
        lines = [
            "WEBVTT",
            "",
            "00:00:01.000 --> 00:00:02.000",
            "Hello",
        ]

        out = translate_lines_native(
            lines,
            api_key="x",
            endpoint="https://api-free.deepl.com/v2/translate",
            target_lang="ZH",
            chunk=10,
            max_retries=0,
            batch_error_callback=lambda start, end, err: errors.append((start, end, err)),
        )

        self.assertEqual(out[3], "Hello")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 1)
        self.assertEqual(errors[0][1], 1)


if __name__ == "__main__":
    unittest.main()
