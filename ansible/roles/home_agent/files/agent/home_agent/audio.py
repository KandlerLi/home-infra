"""Relay browser-recorded audio to OpenAI's transcription API.

Open WebUI's built-in "openai"-compatible STT engine (routers/audio.py's
_transcribe_openai in the pinned v0.11.0 source) POSTs a multipart/form-data
body with a "file" field, a "model" field, and optionally a "language"
field to {api_base_url}/audio/transcriptions. This module is the server
side of that: it parses the incoming body just far enough to extract the
audio file and the language hint, discards whatever model name the caller
sent, and always forwards our own server-configured model -- the same
"pin it here, never trust the caller's stated model" rule api.py's
chat-completions path already applies to HOME_AGENT_MODEL. "language" is
relayed unchanged (it's just an accuracy hint, not cost/security-sensitive
like the model choice), since without it Whisper's auto-detection can
misfire on short clips.
"""

from __future__ import annotations

import email
from email.message import Message

import httpx2

# OpenAI's own transcription endpoint caps uploads at 25 MB; there is no
# reason to accept more than the upstream would.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"


class AudioTranscriptionError(RuntimeError):
    """Indicate that a recorded audio chunk could not be transcribed."""


def _extract_parts(body: bytes, content_type: str) -> tuple[bytes, str, str, str | None]:
    """Return (audio_bytes, filename, content_type, language) from the body.

    "language", if present, is relayed as-is -- unlike "model" this isn't a
    cost- or security-sensitive choice, just an accuracy hint (it's the
    user's own per-chat Settings -> Audio -> Language field, relayed
    unchanged by Open WebUI), so there's no reason to override or drop it
    the way the caller's stated "model" is.
    """
    if not content_type.lower().startswith("multipart/form-data"):
        raise AudioTranscriptionError("expected a multipart/form-data request")

    # email.message_from_bytes needs a header line ahead of the body to know
    # the boundary; we already have that as the request's own Content-Type.
    header = f"Content-Type: {content_type}\r\n\r\n".encode("ascii", "replace")
    message = email.message_from_bytes(header + body)
    if not message.is_multipart():
        raise AudioTranscriptionError("malformed multipart request")

    audio: tuple[bytes, str, str] | None = None
    language: str | None = None
    for part in message.get_payload():
        if not isinstance(part, Message):
            continue
        disposition = part.get("Content-Disposition", "")
        if 'name="file"' in disposition:
            payload = part.get_payload(decode=True)
            if not payload:
                raise AudioTranscriptionError("audio file part was empty")
            filename = part.get_filename() or "audio.webm"
            audio_content_type = part.get_content_type() or "application/octet-stream"
            audio = (payload, filename, audio_content_type)
        elif 'name="language"' in disposition:
            raw = part.get_payload(decode=True)
            if raw:
                language = raw.decode("utf-8", "replace").strip() or None

    if audio is None:
        raise AudioTranscriptionError("no audio file part found")
    return (*audio, language)


class OpenAIAudioTranscriber:
    """Relay one recorded audio chunk to OpenAI, with the model pinned here."""

    def __init__(self, api_key: str, model: str, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def transcribe(self, body: bytes, content_type: str) -> bytes:
        audio_bytes, filename, audio_content_type, language = _extract_parts(
            body, content_type
        )
        data = {"model": self.model}
        if language:
            data["language"] = language
        try:
            with httpx2.Client(timeout=self.timeout) as client:
                response = client.post(
                    TRANSCRIPTIONS_URL,
                    files={"file": (filename, audio_bytes, audio_content_type)},
                    data=data,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx2.HTTPError as error:
            raise AudioTranscriptionError("transcription request failed") from error
        if response.status_code != 200:
            raise AudioTranscriptionError(
                f"transcription upstream returned HTTP {response.status_code}"
            )
        return response.content
