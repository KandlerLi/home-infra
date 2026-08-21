from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ansible/roles/home_agent/files/agent"))

import httpx2

from home_agent import audio


def _build_multipart(
    *, model: str = "whisper-1", filename: str = "chunk.webm", content: bytes = b"fake-audio"
) -> tuple[bytes, str]:
    request = httpx2.Request(
        "POST",
        "http://example.invalid/audio/transcriptions",
        files={"file": (filename, content, "audio/webm")},
        data={"model": model},
    )
    return request.read(), request.headers["content-type"]


def _fake_client(status_code: int, content: bytes, captured: dict) -> MagicMock:
    mock_response = MagicMock(status_code=status_code, content=content)

    def fake_post(url, *, files, data, headers):
        captured["url"] = url
        captured["files"] = files
        captured["data"] = data
        captured["headers"] = headers
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.side_effect = fake_post
    return mock_client


class ExtractAudioPartTests(unittest.TestCase):
    def test_returns_the_file_bytes_filename_and_content_type(self) -> None:
        body, content_type = _build_multipart(content=b"\x1a\x45\xdf\xa3ebml-ish")

        audio_bytes, filename, audio_content_type = audio._extract_audio_part(
            body, content_type
        )

        self.assertEqual(audio_bytes, b"\x1a\x45\xdf\xa3ebml-ish")
        self.assertEqual(filename, "chunk.webm")
        self.assertEqual(audio_content_type, "audio/webm")

    def test_rejects_a_non_multipart_content_type(self) -> None:
        with self.assertRaises(audio.AudioTranscriptionError):
            audio._extract_audio_part(b"{}", "application/json")

    def test_rejects_a_body_missing_the_file_field(self) -> None:
        request = httpx2.Request(
            "POST",
            "http://example.invalid/audio/transcriptions",
            data={"model": "whisper-1"},
        )
        body = request.read()
        content_type = request.headers["content-type"]

        with self.assertRaises(audio.AudioTranscriptionError):
            audio._extract_audio_part(body, content_type)


class OpenAIAudioTranscriberTests(unittest.TestCase):
    def test_pins_its_own_model_regardless_of_the_incoming_model_field(self) -> None:
        # Open WebUI's own AUDIO_STT_MODEL config already constrains this in
        # practice, but home-agent must not trust a client-supplied model
        # any more than the chat path trusts a client-supplied one.
        body, content_type = _build_multipart(model="attacker-chosen-model")
        captured: dict = {}

        with patch(
            "home_agent.audio.httpx2.Client",
            return_value=_fake_client(200, b'{"text":"hello"}', captured),
        ):
            transcriber = audio.OpenAIAudioTranscriber(
                api_key="sk-test", model="whisper-1"
            )
            result = transcriber.transcribe(body, content_type)

        self.assertEqual(captured["data"], {"model": "whisper-1"})
        self.assertEqual(result, b'{"text":"hello"}')

    def test_sends_the_api_key_as_a_bearer_token(self) -> None:
        body, content_type = _build_multipart()
        captured: dict = {}

        with patch(
            "home_agent.audio.httpx2.Client",
            return_value=_fake_client(200, b"{}", captured),
        ):
            audio.OpenAIAudioTranscriber(api_key="sk-secret", model="whisper-1").transcribe(
                body, content_type
            )

        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-secret")

    def test_wraps_a_non_200_upstream_status(self) -> None:
        body, content_type = _build_multipart()

        with patch(
            "home_agent.audio.httpx2.Client",
            return_value=_fake_client(401, b'{"error": "bad key"}', {}),
        ):
            transcriber = audio.OpenAIAudioTranscriber(
                api_key="sk-test", model="whisper-1"
            )
            with self.assertRaises(audio.AudioTranscriptionError):
                transcriber.transcribe(body, content_type)

    def test_wraps_a_connection_failure(self) -> None:
        body, content_type = _build_multipart()
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx2.ConnectError("no route")

        with patch("home_agent.audio.httpx2.Client", return_value=mock_client):
            transcriber = audio.OpenAIAudioTranscriber(
                api_key="sk-test", model="whisper-1"
            )
            with self.assertRaises(audio.AudioTranscriptionError):
                transcriber.transcribe(body, content_type)


if __name__ == "__main__":
    unittest.main()
