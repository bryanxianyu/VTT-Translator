import unittest
from unittest import mock

from translate_vtt_zh_deepl_native import (
    AI_LINE_SEPARATOR,
    GEMINI_BASE_URL,
    OPENAI_RESPONSES_ENDPOINT,
    deepseek_translate_batch,
    gemini_translate_batch,
    openai_translate_batch,
    should_translate,
    translate_lines_native,
)


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

    @mock.patch("translate_vtt_zh_deepl_native.deepl_translate_batch")
    def test_concurrent_batches_preserve_output_positions(self, mock_translate):
        mock_translate.side_effect = lambda texts, **_kwargs: [f"ZH:{text}" for text in texts]
        lines = [
            "WEBVTT",
            "",
            "00:00:01.000 --> 00:00:02.000",
            "First",
            "Second",
            "00:00:02.000 --> 00:00:03.000",
            "Third",
        ]

        out = translate_lines_native(
            lines,
            api_key="x",
            endpoint="https://api-free.deepl.com/v2/translate",
            target_lang="ZH",
            chunk=1,
            concurrency=3,
            max_retries=0,
            log_progress=False,
        )

        self.assertEqual(out[3], "ZH:First")
        self.assertEqual(out[4], "ZH:Second")
        self.assertEqual(out[6], "ZH:Third")


class OpenAITests(unittest.TestCase):
    @mock.patch("translate_vtt_zh_deepl_native.requests.post")
    def test_openai_batch_parses_json_array_output(self, mock_post):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output_text": f"你好{AI_LINE_SEPARATOR}世界"
        }
        mock_post.return_value = mock_response

        out = openai_translate_batch(
            ["hello", "world"],
            api_key="k",
            target_lang="ZH",
            endpoint=OPENAI_RESPONSES_ENDPOINT,
            max_retries=0,
        )
        self.assertEqual(out, ["你好", "世界"])


class DeepSeekTests(unittest.TestCase):
    @mock.patch("translate_vtt_zh_deepl_native.requests.post")
    def test_deepseek_batch_parses_chat_completions(self, mock_post):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": f"你好{AI_LINE_SEPARATOR}世界"
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        out = deepseek_translate_batch(
            ["hello", "world"],
            api_key="k",
            target_lang="ZH",
            endpoint="https://api.deepseek.com/chat/completions",
            max_retries=0,
        )
        self.assertEqual(out, ["你好", "世界"])


class GeminiTests(unittest.TestCase):
    @mock.patch("translate_vtt_zh_deepl_native.requests.post")
    def test_gemini_batch_parses_generate_content(self, mock_post):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": f"你好{AI_LINE_SEPARATOR}世界"}
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        out = gemini_translate_batch(
            ["hello", "world"],
            api_key="k",
            target_lang="ZH",
            endpoint=GEMINI_BASE_URL,
            model="gemini-3.1-flash-lite-preview",
            max_retries=0,
        )
        self.assertEqual(out, ["你好", "世界"])


if __name__ == "__main__":
    unittest.main()
