"""Run a minimal xAI connectivity diagnostic.

Reads XAI_API_KEY from the environment and writes artifacts under outputs/diagnostics.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from pipeline import _tool, have_ffmpeg


BASE = "https://api.x.ai/v1"


def post_json(path: str, api_key: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_bytes(path: str, api_key: str, payload: dict, timeout: int = 120) -> bytes:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_json(path: str, api_key: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AutoVideoMachineDiagnostic/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
        body = exc.read().decode("utf-8", errors="ignore")[:800]
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
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        print("Missing XAI_API_KEY environment variable")
        return 2

    out_dir = Path("outputs") / "diagnostics" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(out_dir.resolve()),
        "steps": {},
    }

    def check_ffmpeg():
        return {
            "have_ffmpeg": have_ffmpeg(),
            "ffmpeg": _tool("ffmpeg"),
            "ffprobe": _tool("ffprobe"),
        }

    def check_chat():
        resp = post_json(
            "/chat/completions",
            api_key,
            {
                "model": os.environ.get("XAI_TEXT_MODEL", "grok-4.5"),
                "messages": [
                    {"role": "system", "content": "You are a concise API diagnostic assistant."},
                    {"role": "user", "content": "Reply with exactly: xAI text ok"},
                ],
                "temperature": 0,
            },
        )
        text = resp["choices"][0]["message"]["content"]
        return {"model": resp.get("model"), "reply": text[:100]}

    def check_tts():
        audio = post_bytes(
            "/tts",
            api_key,
            {
                "text": "这是自动短视频系统的配音连通性测试。",
                "voice_id": os.environ.get("XAI_TTS_VOICE", "eve"),
                "language": "zh",
                "output_format": {"codec": "mp3", "sample_rate": 44100, "bit_rate": 128000},
            },
        )
        audio_path = out_dir / "xai_tts_test.mp3"
        audio_path.write_bytes(audio)
        return {"path": str(audio_path.resolve()), "bytes": len(audio), "ffprobe": ffprobe(audio_path)}

    def check_video():
        start = post_json(
            "/videos/generations",
            api_key,
            {
                "model": os.environ.get("XAI_VIDEO_MODEL", "grok-imagine-video"),
                "prompt": (
                    "Vertical 9:16 cinematic short video, 3 seconds. A clean modern desk with "
                    "a laptop showing an automated video production dashboard, warm practical light, "
                    "subtle camera push-in, no text overlays, realistic style."
                ),
                "duration": 3,
                "aspect_ratio": "9:16",
                "resolution": "720p",
            },
            timeout=120,
        )
        request_id = start.get("request_id") or start.get("id")
        if not request_id:
            return {"start_response": start, "error": "No request_id returned"}

        last = None
        deadline = time.time() + 900
        while time.time() < deadline:
            last = get_json(f"/videos/{request_id}", api_key)
            status = str(last.get("status", "")).lower()
            if status == "done":
                url = ((last.get("video") or {}).get("url") or last.get("url"))
                if not url:
                    return {"request_id": request_id, "status": status, "error": "No video URL", "response": last}
                video = download(url)
                video_path = out_dir / "xai_video_3s_test.mp4"
                video_path.write_bytes(video)
                return {
                    "request_id": request_id,
                    "status": status,
                    "path": str(video_path.resolve()),
                    "bytes": len(video),
                    "ffprobe": ffprobe(video_path),
                }
            if status in {"failed", "expired", "error", "canceled", "cancelled"}:
                return {"request_id": request_id, "status": status, "response": last}
            time.sleep(5)
        return {"request_id": request_id, "status": "timeout", "last_response": last}

    record_step(report, "ffmpeg", check_ffmpeg)
    record_step(report, "text_chat", check_chat)
    record_step(report, "tts", check_tts)
    record_step(report, "video_3s", check_video)

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(step.get("ok") for step in report["steps"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
