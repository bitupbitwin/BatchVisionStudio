"""Run a minimal MiniMax connectivity diagnostic.

Reads MINIMAX_API_KEY from the environment and writes artifacts under outputs/diagnostics.
Optional environment variables:
MINIMAX_GROUP_ID, MINIMAX_BASE_URL, MINIMAX_TTS_MODEL, MINIMAX_TTS_VOICE, MINIMAX_VIDEO_MODEL.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
from datetime import datetime
from pathlib import Path

from pipeline import _tool, have_ffmpeg
from providers import MiniMaxTTSClient, MiniMaxVideoClient


def ffprobe(path: Path) -> dict:
    import subprocess

    if not have_ffmpeg():
        return {"ok": False, "error": "ffmpeg/ffprobe not found"}
    proc = subprocess.run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()[:500]}
    return {"ok": True, "data": json.loads(proc.stdout or "{}")}


def record_step(report: dict, name: str, fn):
    started = time.time()
    try:
        result = fn()
        report["steps"][name] = {
            "ok": True,
            "seconds": round(time.time() - started, 2),
            **(result or {}),
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")[:1000]
        report["steps"][name] = {
            "ok": False,
            "seconds": round(time.time() - started, 2),
            "error": f"HTTP {exc.code}: {body}",
        }
    except Exception as exc:
        report["steps"][name] = {
            "ok": False,
            "seconds": round(time.time() - started, 2),
            "error": str(exc),
        }


def main() -> int:
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        print("Missing MINIMAX_API_KEY environment variable")
        return 2

    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1").strip()
    group_id = os.environ.get("MINIMAX_GROUP_ID", "").strip()
    out_dir = Path("outputs") / "diagnostics" / datetime.now().strftime("%Y%m%d_%H%M%S_minimax")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(out_dir.resolve()),
        "base_url": base_url,
        "steps": {},
    }

    def check_ffmpeg():
        return {
            "have_ffmpeg": have_ffmpeg(),
            "ffmpeg": _tool("ffmpeg"),
            "ffprobe": _tool("ffprobe"),
        }

    def check_tts():
        client = MiniMaxTTSClient(
            api_key,
            group_id=group_id,
            model=os.environ.get("MINIMAX_TTS_MODEL", "speech-02-hd"),
            voice=os.environ.get("MINIMAX_TTS_VOICE", "Chinese (Mandarin)_Warm_Girl"),
            base_url=base_url,
        )
        audio = client.synthesize("这是自动短视频系统的 MiniMax 配音连通性测试。")
        audio_path = out_dir / "minimax_tts_test.mp3"
        audio_path.write_bytes(audio)
        return {"path": str(audio_path.resolve()), "bytes": len(audio), "ffprobe": ffprobe(audio_path)}

    def check_video():
        client = MiniMaxVideoClient(
            api_key,
            model=os.environ.get("MINIMAX_VIDEO_MODEL", "MiniMax-Hailuo-02"),
            base_url=base_url,
        )
        video = client.generate_video(
            (
                "Vertical 9:16 cinematic short video, 3 seconds. A clean modern desk with "
                "a laptop showing an automated video production dashboard, warm practical light, "
                "subtle camera push-in, no text overlays, realistic style."
            ),
            duration=3,
            aspect_ratio="9:16",
            resolution="720p",
            timeout=900,
        )
        video_path = out_dir / "minimax_video_3s_test.mp4"
        video_path.write_bytes(video)
        return {"path": str(video_path.resolve()), "bytes": len(video), "ffprobe": ffprobe(video_path)}

    record_step(report, "ffmpeg", check_ffmpeg)
    record_step(report, "tts", check_tts)
    record_step(report, "video_3s", check_video)

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(step.get("ok") for step in report["steps"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
