"""YouTube metadata/subtitle extraction via optional yt-dlp.

Never auto-installs yt-dlp. When the optional dependency is missing, callers
should fall through to the generic HTTP pipeline with a warning.
"""

from __future__ import annotations

import importlib.util
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .domains import detect_platform, is_safe_public_http_url

DEFAULT_USER_AGENT = "supersocks-url-scraper/0.2"
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024


def yt_dlp_available() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


def _trim(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    space = cut.rfind(" ", 0, max_chars)
    return ((cut[:space] if space >= int(max_chars * 0.5) else cut[: max_chars - 1]) + "…").strip()


def _parse_vtt_or_srt(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("WEBVTT") or value.startswith("NOTE"):
            continue
        if re.fullmatch(r"\d+", value):
            continue
        if "-->" in value:
            continue
        if value.startswith("Kind:") or value.startswith("Language:"):
            continue
        lines.append(value)
    return " ".join(lines)


def _pick_subtitle_track(info: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (url, source) preferring manual subs then auto-captions."""
    for source_key, source_label in (("subtitles", "manual"), ("automatic_captions", "auto-captions")):
        tracks = info.get(source_key) or {}
        if not isinstance(tracks, dict):
            continue
        # Prefer common languages, then first available.
        preferred = ["en", "en-US", "en-GB", "fr", "fr-FR", "es", "de"]
        ordered_langs = [lang for lang in preferred if lang in tracks] + [lang for lang in tracks if lang not in preferred]
        for lang in ordered_langs:
            entries = tracks.get(lang) or []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                url = entry.get("url")
                ext = str(entry.get("ext") or "").lower()
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    if ext in {"", "vtt", "srt", "ttml", "srv3", "srv2", "srv1", "json3"}:
                        return url, source_label
    return None, None


def _fetch_text(url: str, *, timeout: int) -> str:
    if not is_safe_public_http_url(url):
        raise ValueError("subtitle URL blocked by safety policy")
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_SUBTITLE_BYTES + 1)
    if len(raw) > MAX_SUBTITLE_BYTES:
        raise ValueError(f"subtitle track exceeds {MAX_SUBTITLE_BYTES} bytes")
    return raw.decode("utf-8", errors="replace")


def _format_upload_date(value: object) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text or None


def extract_youtube(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 20,
    ydl_factory: Any | None = None,
    subtitle_fetcher: Any | None = None,
) -> dict[str, Any] | None:
    """Extract YouTube metadata/subtitles without downloading media.

    Returns None when yt-dlp is unavailable so the generic pipeline can run.
    """
    if detect_platform(url) != "youtube" or not is_safe_public_http_url(url):
        return None
    if ydl_factory is None and not yt_dlp_available():
        return None

    max_chars = max(50, min(int(length or 900), 10_000))
    warnings: list[str] = []

    try:
        if ydl_factory is not None:
            ydl = ydl_factory()
            info = ydl.extract_info(url, download=False)
        else:
            import yt_dlp

            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "nocheckcertificate": False,
                "extract_flat": False,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "yt-dlp",
            "status": "partial",
            "warnings": [f"yt-dlp extraction failed: {exc}"],
            "platform": "youtube",
        }

    if not isinstance(info, dict):
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "yt-dlp",
            "status": "partial",
            "warnings": ["yt-dlp returned no metadata"],
            "platform": "youtube",
        }

    title = str(info.get("title") or "").strip() or None
    author = str(info.get("uploader") or info.get("channel") or info.get("creator") or "").strip() or None
    description = str(info.get("description") or "").strip()
    duration = info.get("duration")
    try:
        duration_seconds = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    published_at = _format_upload_date(info.get("upload_date") or info.get("release_date"))
    final_url = str(info.get("webpage_url") or info.get("original_url") or url)

    transcript = ""
    transcript_source = None
    sub_url, source = _pick_subtitle_track(info)
    if sub_url:
        if not is_safe_public_http_url(sub_url):
            warnings.append("subtitle URL blocked by safety policy")
        else:
            try:
                fetcher = subtitle_fetcher or _fetch_text
                raw_sub = fetcher(sub_url, timeout=timeout)
                if str(sub_url).endswith(".json3") or raw_sub.lstrip().startswith("{"):
                    try:
                        data = json.loads(raw_sub)
                        events = data.get("events") if isinstance(data, dict) else None
                        parts: list[str] = []
                        if isinstance(events, list):
                            for event in events:
                                if not isinstance(event, dict):
                                    continue
                                for seg in event.get("segs") or []:
                                    if isinstance(seg, dict) and seg.get("utf8"):
                                        parts.append(str(seg["utf8"]))
                        transcript = " ".join(parts)
                    except json.JSONDecodeError:
                        transcript = _parse_vtt_or_srt(raw_sub)
                else:
                    transcript = _parse_vtt_or_srt(raw_sub)
                transcript_source = source
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                warnings.append(f"subtitle fetch failed: {exc}")
    else:
        warnings.append("no subtitles/auto-captions available")

    body = transcript or description
    summary = _trim(body or title or "", max_chars)
    status = "ok" if summary else "partial"
    if not transcript and description:
        warnings.append("summary sourced from video description")
    if not summary:
        warnings.append("yt-dlp metadata extracted but no readable text")

    payload: dict[str, Any] = {
        "url": final_url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": "yt-dlp",
        "status": status,
        "warnings": warnings,
        "platform": "youtube",
        "author": author,
        "published_at": published_at,
        "duration": duration_seconds,
        "transcript_source": transcript_source,
    }
    if include_content:
        payload["content"] = body or ""
    if transcript:
        payload["transcript"] = transcript
    return payload
