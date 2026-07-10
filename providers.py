"""外部生成式 API 客户端：Grok 视频/图像（xAI）、Gemini TTS（Google）、MiniMax。

仅依赖标准库 urllib。所有响应解析都做了多形态兜底，因为各家字段可能随版本微调；
若某家返回结构与此处不符，改动集中在本文件即可。
"""

import base64
import json
import re
import time
import urllib.parse
import urllib.request


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_bytes(url: str, headers: dict, payload: dict, timeout: int = 120) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_bytes(url: str, timeout: int = 120) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    req = urllib.request.Request(url, headers={"User-Agent": "VideoAutoStudio/0.3"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_bytes(url: str, headers: dict | None = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _data_uri(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


def _dig(obj, *paths):
    """按多组路径尝试取值，返回第一个命中的非空值。path 为 key/索引序列。"""
    for path in paths:
        cur = obj
        ok = True
        for key in path:
            try:
                cur = cur[key]
            except (KeyError, IndexError, TypeError):
                ok = False
                break
        if ok and cur:
            return cur
    return None


class GrokClient:
    """xAI Grok Imagine：图像生成 + 文/图生视频（异步轮询）。"""

    BASE = "https://api.x.ai/v1"

    def __init__(self, api_key: str, video_model: str = "grok-imagine-video",
                 image_model: str = "grok-2-image"):
        if not api_key:
            raise RuntimeError("未配置 xAI(Grok) API Key")
        self.api_key = api_key
        self.video_model = video_model
        self.image_model = image_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate_image(self, prompt: str, timeout: int = 180) -> bytes:
        resp = _post_json(
            f"{self.BASE}/images/generations",
            self._headers(),
            {"model": self.image_model, "prompt": prompt, "n": 1, "response_format": "b64_json"},
            timeout=timeout,
        )
        b64 = _dig(resp, ["data", 0, "b64_json"])
        if b64:
            return base64.b64decode(b64)
        url = _dig(resp, ["data", 0, "url"])
        if url:
            return _download_bytes(url)
        raise RuntimeError(f"图像生成返回异常：{json.dumps(resp)[:300]}")

    def generate_video(self, prompt: str, image_bytes: bytes | None = None, duration: int = 6,
                       aspect_ratio: str = "9:16", resolution: str = "720p",
                       poll_interval: int = 5, timeout: int = 900) -> bytes:
        payload = {
            "model": self.video_model,
            "prompt": prompt,
            "duration": int(duration),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if image_bytes:
            # 图生视频：以参考图/上一镜尾帧作为首帧（数据 URI）
            payload["image"] = {"url": _data_uri(image_bytes)}

        start = _post_json(f"{self.BASE}/videos/generations", self._headers(), payload, timeout=120)
        # 同步即返回（部分实现）
        direct = _dig(start, ["url"], ["video", "url"], ["data", 0, "url"])
        if direct:
            return _download_bytes(direct)

        request_id = _dig(start, ["request_id"], ["id"])
        if not request_id:
            raise RuntimeError(f"视频生成未返回 request_id：{json.dumps(start)[:300]}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            status_resp = _get_json(f"{self.BASE}/videos/{request_id}", self._headers(), timeout=60)
            status = str(_dig(status_resp, ["status"]) or "").lower()
            if status in {"done", "succeeded", "completed", "success"}:
                url = _dig(status_resp, ["url"], ["video", "url"], ["data", 0, "url"], ["output", "url"])
                if not url:
                    raise RuntimeError(f"视频已完成但未找到下载地址：{json.dumps(status_resp)[:300]}")
                return _download_bytes(url)
            if status in {"failed", "error", "canceled", "cancelled"}:
                raise RuntimeError(f"视频生成失败：{json.dumps(status_resp)[:300]}")
            time.sleep(poll_interval)
        raise RuntimeError("视频生成超时")


class GeminiTTSClient:
    """Google Gemini 文本转语音，返回 WAV 字节。"""

    BASE = "https://generativelanguage.googleapis.com/v1beta"
    audio_ext = "wav"

    def __init__(self, api_key: str, model: str = "gemini-3.1-flash-tts-preview", voice: str = "Kore"):
        self.api_key = api_key
        self.model = model
        self.voice = voice

    def synthesize(self, text: str, voice: str | None = None, timeout: int = 120) -> bytes:
        if not self.api_key:
            raise RuntimeError("未配置 Gemini API Key")
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or self.voice}}
                },
            },
        }
        resp = _post_json(
            f"{self.BASE}/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key},
            payload,
            timeout=timeout,
        )
        part = _dig(resp, ["candidates", 0, "content", "parts", 0, "inlineData"],
                    ["candidates", 0, "content", "parts", 0, "inline_data"])
        if not part:
            raise RuntimeError(f"TTS 返回异常：{json.dumps(resp)[:300]}")
        b64 = part.get("data")
        mime = part.get("mimeType") or part.get("mime_type") or "audio/L16;rate=24000"
        pcm = base64.b64decode(b64)
        rate_match = re.search(r"rate=(\d+)", mime)
        rate = int(rate_match.group(1)) if rate_match else 24000
        return pcm16_to_wav(pcm, rate)


class XAITTSClient:
    """xAI 文本转语音，返回 MP3 字节。"""

    BASE = "https://api.x.ai/v1"
    audio_ext = "mp3"

    def __init__(self, api_key: str, voice: str = "eve", language: str = "zh"):
        if not api_key:
            raise RuntimeError("未配置 xAI API Key")
        self.api_key = api_key
        self.voice = voice or "eve"
        self.language = language or "zh"

    def synthesize(self, text: str, voice: str | None = None, timeout: int = 120) -> bytes:
        payload = {
            "text": text,
            "voice_id": voice or self.voice,
            "language": self.language,
            "output_format": {
                "codec": "mp3",
                "sample_rate": 44100,
                "bit_rate": 128000,
            },
        }
        return _post_bytes(
            f"{self.BASE}/tts",
            {"Authorization": f"Bearer {self.api_key}"},
            payload,
            timeout=timeout,
        )


class MiniMaxTTSClient:
    """MiniMax 同步语音合成，默认兼容 t2a_v2 形态，返回 MP3 字节。"""

    audio_ext = "mp3"

    def __init__(
        self,
        api_key: str,
        group_id: str = "",
        model: str = "speech-02-hd",
        voice: str = "Chinese (Mandarin)_Warm_Girl",
        base_url: str = "https://api.minimax.chat/v1",
    ):
        if not api_key:
            raise RuntimeError("未配置 MiniMax API Key")
        self.api_key = api_key
        self.group_id = group_id
        self.model = model or "speech-02-hd"
        self.voice = voice or "Chinese (Mandarin)_Warm_Girl"
        self.base_url = (base_url or "https://api.minimax.chat/v1").rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def synthesize(self, text: str, voice: str | None = None, timeout: int = 120) -> bytes:
        query = f"?GroupId={urllib.parse.quote(self.group_id)}" if self.group_id else ""
        payload = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice or self.voice,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        resp = _post_json(f"{self.base_url}/t2a_v2{query}", self._headers(), payload, timeout=timeout)
        audio = _dig(resp, ["data", "audio"], ["audio"])
        if not audio:
            raise RuntimeError(f"MiniMax TTS 返回异常：{json.dumps(resp, ensure_ascii=False)[:500]}")
        if isinstance(audio, str):
            try:
                return bytes.fromhex(audio)
            except ValueError:
                return base64.b64decode(audio)
        raise RuntimeError(f"MiniMax TTS 音频字段格式异常：{type(audio).__name__}")


class MiniMaxVideoClient:
    """MiniMax/Hailuo 文生视频客户端，默认兼容 video_generation 任务形态。"""

    def __init__(
        self,
        api_key: str,
        model: str = "MiniMax-Hailuo-02",
        base_url: str = "https://api.minimax.chat/v1",
    ):
        if not api_key:
            raise RuntimeError("未配置 MiniMax API Key")
        self.api_key = api_key
        self.model = model or "MiniMax-Hailuo-02"
        self.base_url = (base_url or "https://api.minimax.chat/v1").rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate_image(self, prompt: str, timeout: int = 180) -> bytes:
        payload = {
            "model": "image-01",
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "response_format": "url",
            "n": 1,
        }
        resp = _post_json(f"{self.base_url}/image_generation", self._headers(), payload, timeout=timeout)
        url = _dig(resp, ["data", "image_urls", 0], ["data", 0, "url"], ["image_url"], ["url"])
        if url:
            return _download_bytes(url)
        b64 = _dig(resp, ["data", 0, "b64_json"], ["b64_json"])
        if b64:
            return base64.b64decode(b64)
        raise RuntimeError(f"MiniMax 图像生成返回异常：{json.dumps(resp, ensure_ascii=False)[:500]}")

    def generate_video(
        self,
        prompt: str,
        image_bytes: bytes | None = None,
        duration: int = 6,
        aspect_ratio: str = "9:16",
        resolution: str = "720p",
        poll_interval: int = 5,
        timeout: int = 900,
    ) -> bytes:
        if image_bytes:
            raise RuntimeError("MiniMax 视频客户端当前仅接入文生视频；图生视频需要按官方最新上传文件接口补齐。")
        payload = {
            "model": self.model,
            "prompt": prompt,
            "duration": int(duration),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        start = _post_json(f"{self.base_url}/video_generation", self._headers(), payload, timeout=120)
        direct = _dig(start, ["url"], ["video", "url"], ["data", 0, "url"])
        if direct:
            return _download_bytes(direct)
        task_id = _dig(start, ["task_id"], ["id"], ["data", "task_id"])
        if not task_id:
            raise RuntimeError(f"MiniMax 视频生成未返回 task_id：{json.dumps(start, ensure_ascii=False)[:500]}")

        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = _get_json(f"{self.base_url}/query/video_generation?task_id={urllib.parse.quote(str(task_id))}",
                             self._headers(), timeout=60)
            status = str(_dig(last, ["status"], ["data", "status"]) or "").lower()
            if status in {"success", "succeeded", "done", "completed"}:
                url = _dig(last, ["file", "download_url"], ["video", "url"], ["url"], ["data", "url"])
                if url:
                    return _download_bytes(url)
                file_id = _dig(last, ["file_id"], ["data", "file_id"], ["data", "video_file_id"])
                if file_id:
                    return _get_bytes(
                        f"{self.base_url}/files/retrieve?file_id={urllib.parse.quote(str(file_id))}",
                        self._headers(),
                        timeout=180,
                    )
                raise RuntimeError(f"MiniMax 视频已完成但未找到下载地址：{json.dumps(last, ensure_ascii=False)[:500]}")
            if status in {"failed", "error", "fail", "canceled", "cancelled"}:
                raise RuntimeError(f"MiniMax 视频生成失败：{json.dumps(last, ensure_ascii=False)[:500]}")
            time.sleep(poll_interval)
        raise RuntimeError(f"MiniMax 视频生成超时：{json.dumps(last or {}, ensure_ascii=False)[:500]}")


def pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """把裸 16-bit PCM 包装成 WAV。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()
