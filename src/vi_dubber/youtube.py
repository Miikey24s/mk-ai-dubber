from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .runtime import ffmpeg_exe


ProgressCallback = Callable[[float, str], None]
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in YOUTUBE_HOSTS


def download_youtube(
    url: str,
    output_dir: Path,
    progress_callback: ProgressCallback | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not is_youtube_url(url):
        raise ValueError("Please provide a valid YouTube video URL.")

    import yt_dlp

    output_dir.mkdir(parents=True, exist_ok=True)

    def hook(data: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        status = data.get("status")
        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            fraction = downloaded / total if total > 0 else 0.05
            progress_callback(min(0.95, max(0.01, fraction)), "Đang tải video YouTube")
        elif status == "finished":
            progress_callback(0.98, "Đang ghép các luồng YouTube")

    options = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "ffmpeg_location": str(ffmpeg_exe().parent),
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url.strip(), download=True)
        if info is None:
            raise RuntimeError("yt-dlp did not return video metadata.")
        video_id = str(info.get("id") or "").strip()
        if not video_id:
            raise RuntimeError("YouTube video id is missing.")

    candidates = [
        path
        for path in output_dir.glob(f"{video_id}.*")
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
    ]
    if not candidates:
        raise RuntimeError("Downloaded YouTube media file was not found after merge.")
    media_path = max(candidates, key=lambda path: path.stat().st_size)

    metadata = {
        "id": video_id,
        "title": str(info.get("title") or video_id),
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "duration_seconds": float(info.get("duration") or 0.0),
        "webpage_url": str(info.get("webpage_url") or url.strip()),
    }
    if progress_callback is not None:
        progress_callback(1.0, "Đã tải xong video YouTube")
    return media_path, metadata
