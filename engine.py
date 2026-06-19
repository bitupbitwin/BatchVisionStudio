"""核心逻辑层：故事编排、脚本生成、模型调用与项目存储。

这一层不依赖任何 UI 框架，可被 PyWebView 桥接层（app.py）或测试直接调用。
"""

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "outputs"
CONFIG_PATH = APP_DIR / "config.json"
UI_CONFIG_PATH = APP_DIR / "ui_config.json"

THEMES = ("orange", "red", "blue", "green", "purple", "pink", "yellow")


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(text: str, fallback: str = "untitled") -> str:
    text = re.sub(r"[\\/:*?\"<>|：＊？“”《》｜\r\n\t]+", "_", text).strip(" ._")
    text = re.sub(r"\s+", "_", text)
    return text[:60] or fallback


def compact_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, size: int) -> list[str]:
    """按句子边界把长文切成约 size 字一块，尽量不截断句子。"""
    pieces = re.split(r"(?<=[。！？!?；;\n])", text)
    chunks, cur = [], ""
    for piece in pieces:
        if cur and len(cur) + len(piece) > size:
            chunks.append(cur)
            cur = piece
        else:
            cur += piece
    if cur.strip():
        chunks.append(cur)
    return chunks or [text]


class PlainTextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip = True
        if tag in {"p", "br", "div", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            value = data.strip()
            if value:
                self.parts.append(value)

    def text(self) -> str:
        return compact_text(" ".join(self.parts))


def fetch_url_text(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VideoAutoStudio/0.2 (+local desktop app)",
            "Accept": "text/html,application/xhtml+xml,text/plain",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
    decoded = raw.decode(charset, errors="ignore")
    parser = PlainTextHTMLParser()
    parser.feed(decoded)
    text = parser.text()
    return text or compact_text(decoded)


@dataclass
class ScriptItem:
    index: int
    title: str
    summary: str
    narration: str
    shots: list

    def to_dict(self) -> dict:
        return asdict(self)


class ProjectStorage:
    def __init__(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        self.current_dir = None

    def create_story_project(self, title: str, source_kind: str, source_preview: str) -> Path:
        folder_name = f"{now_id()}_{safe_name(title, 'story')}"
        self.current_dir = OUTPUT_DIR / folder_name
        (self.current_dir / "story").mkdir(parents=True, exist_ok=True)
        (self.current_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (self.current_dir / "runs").mkdir(parents=True, exist_ok=True)
        self.write_json(
            self.current_dir / "metadata.json",
            {
                "title": title,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "source_kind": source_kind,
                "source_preview": source_preview[:500],
            },
        )
        return self.current_dir

    def save_story(self, story: dict) -> None:
        assert self.current_dir
        self.write_json(self.current_dir / "story" / "story.json", story)
        (self.current_dir / "story" / "story.md").write_text(
            f"# {story['title']}\n\n## 故事梗概\n\n{story['outline']}\n\n"
            f"## 主要人物/要素\n\n{story['characters']}\n\n"
            f"## 视频风格\n\n{story['style']}\n",
            encoding="utf-8",
        )

    def save_scripts(self, scripts: list[ScriptItem]) -> None:
        assert self.current_dir
        scripts_dir = self.current_dir / "scripts"
        for item in scripts:
            stem = f"{item.index:03d}_{safe_name(item.title)}"
            self.write_json(scripts_dir / f"{stem}.json", item.to_dict())
            shot_text = "\n".join(
                f"{i + 1}. 时长: {shot.get('duration', '')} 秒\n"
                f"   画面提示词: {shot.get('visual_prompt', '')}\n"
                f"   旁白: {shot.get('voiceover', '')}"
                for i, shot in enumerate(item.shots)
            )
            (scripts_dir / f"{stem}.md").write_text(
                f"# {item.title}\n\n## 摘要\n\n{item.summary}\n\n"
                f"## 旁白脚本\n\n{item.narration}\n\n## 镜头提示词\n\n{shot_text}\n",
                encoding="utf-8",
            )

    def save_run(self, item: ScriptItem, status: str, note: str) -> Path:
        assert self.current_dir
        run_path = self.current_dir / "runs" / f"{now_id()}_{item.index:03d}_{safe_name(item.title)}.json"
        self.write_json(
            run_path,
            {
                "script_index": item.index,
                "title": item.title,
                "status": status,
                "note": note,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "next_step": "接入视频生成 API 后，在这里记录素材、音频、画面和最终视频文件路径。",
            },
        )
        return run_path

    @staticmethod
    def write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# 可保存的配置项；新增 Grok 视频与 Gemini TTS 相关字段
CONFIG_STR_KEYS = (
    "api_url", "api_key", "model",
    "xai_api_key", "xai_video_model", "xai_image_model",
    "gemini_api_key", "gemini_tts_model", "gemini_voice",
    "video_aspect_ratio", "video_resolution", "consistency",
)


def save_config(config: dict) -> None:
    merged = load_config()
    for key in CONFIG_STR_KEYS:
        if key in config:
            value = str(config.get(key, "")).strip()
            # 留空的密钥/字段沿用旧值，避免误清空
            if value or key not in merged:
                merged[key] = value
    if "temperature" in config or "temperature" not in merged:
        try:
            merged["temperature"] = float(config.get("temperature", merged.get("temperature", 0.7)))
        except (TypeError, ValueError):
            merged["temperature"] = 0.7
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ui_config() -> dict:
    if not UI_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(UI_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def get_theme() -> str:
    theme = load_ui_config().get("theme", "orange")
    return theme if theme in THEMES else "orange"


def save_theme(theme: str) -> str:
    theme = theme if theme in THEMES else "orange"
    ui = load_ui_config()
    ui["theme"] = theme
    UI_CONFIG_PATH.write_text(json.dumps(ui, ensure_ascii=False, indent=2), encoding="utf-8")
    return theme


class ModelClient:
    def __init__(self):
        self.config = load_config()

    def reload(self) -> None:
        self.config = load_config()

    def available(self) -> bool:
        return bool(self.config.get("api_url") and self.config.get("api_key"))

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.available():
            raise RuntimeError("未配置 API")
        payload = {
            "model": self.config.get("model") or "gpt-4.1",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.get("temperature", 0.7),
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.config["api_url"],
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config['api_key']}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]


class StoryEngine:
    def __init__(self):
        self.model = ModelClient()

    def arrange_story(self, text: str) -> dict:
        text = compact_text(text)
        if self.model.available():
            try:
                return self._arrange_with_model(text)
            except Exception:
                pass
        return self._arrange_locally(text)

    def _arrange_with_model(self, text: str) -> dict:
        # 长文先用 map-reduce 通读全文压缩成全局摘要，避免只看开头被截断
        digest = self._summarize_long(text, target_chars=10000) if len(text) > 12000 else text
        prompt = (
            "请把下面素材编排为适合短视频系列的故事。只输出 JSON，字段为 "
            "title, outline, characters, style。素材已是覆盖全文的摘要，outline "
            "要贯穿开头、发展、高潮、结尾，保留核心情节和信息密度。\n\n"
            f"{digest[:12000]}"
        )
        raw = self.model.chat("你是短视频故事编排与分镜策划专家。", prompt)
        return json.loads(self._extract_json(raw))

    def _summarize_long(self, text: str, target_chars: int) -> str:
        """递归 map-reduce：把任意长度的文本压缩到 target_chars 以内且覆盖全文。"""
        if len(text) <= target_chars:
            return text
        chunks = chunk_text(text, 6000)
        if len(chunks) == 1:
            return text[:target_chars]
        summaries = []
        for i, ch in enumerate(chunks, 1):
            out = self.model.chat(
                "你是长篇内容情节摘要助手。",
                f"这是全文的第 {i}/{len(chunks)} 部分，请用中文提炼这一部分的关键情节、"
                f"人物与转折，保留信息密度，输出 300-500 字摘要：\n\n{ch}",
            )
            summaries.append(f"【第{i}部分】{out.strip()}")
        combined = "\n\n".join(summaries)
        return self._summarize_long(combined, target_chars)  # 仍过长则继续归并

    def _arrange_locally(self, text: str) -> dict:
        title = self._guess_title(text)
        sentences = self._sentences(text)
        outline = self._coverage_digest(sentences, 3000) if sentences else text[:1200]
        keywords = self._keywords(text)
        return {
            "title": title,
            "outline": outline,
            "characters": "、".join(keywords[:10]) or "待大模型进一步提取",
            "style": "节奏清晰、画面感强、适合 1 分钟短视频；先给出冲突或钩子，再推进关键事件，最后留下转折或结论。",
        }

    def _coverage_digest(self, sentences: list[str], max_chars: int) -> str:
        """从全篇均匀采样句子，确保覆盖开头/中间/结尾，控制在 max_chars 内。"""
        joined = " ".join(sentences)
        if len(joined) <= max_chars:
            return joined
        n = len(sentences)
        last = sentences[-1]
        # 预留结尾句的位置，保证全篇的结局一定进入摘要
        budget = max(0, max_chars - len(last) - 1)
        avg = max(1, len(joined) // n)
        take = max(2, min(n, max_chars // avg))
        idxs = sorted({int(i * (n - 1) / (take - 1)) for i in range(take)})
        picked, acc = [], 0
        for i in idxs:
            if i == n - 1:
                continue
            s = sentences[i]
            if acc + len(s) + 1 > budget:
                break
            picked.append(s)
            acc += len(s) + 1
        picked.append(last)
        return " ".join(picked)[:max_chars]

    def generate_scripts(self, story: dict, source_text: str, seconds_per_video: int = 60,
                         target_count: int = 0) -> list[ScriptItem]:
        if self.model.available():
            try:
                return self._scripts_with_model(story, source_text, seconds_per_video, target_count)
            except Exception:
                pass
        return self._scripts_locally(story, source_text, seconds_per_video, target_count)

    def _scripts_with_model(self, story: dict, source_text: str, seconds_per_video: int,
                            target_count: int) -> list[ScriptItem]:
        total = target_count if target_count > 0 else max(1, math.ceil(len(source_text) / 1800))
        # 长文用覆盖全篇的摘录作上下文，避免只截取开头
        src = source_text
        if len(src) > 16000:
            src = self._coverage_digest(self._sentences(src), 15000)
        prompt = (
            f"请根据故事生成 {total} 个短视频脚本，每个约 {seconds_per_video} 秒。"
            "只输出 JSON 数组，每项字段为 index,title,summary,narration,shots。"
            "shots 每项字段为 duration,visual_prompt,voiceover。"
            "title 要高度概括该集核心内容并具有吸引力（像爆款短视频标题，可制造悬念或好奇，"
            "但不剧透关键反转），不超过 18 字。\n\n"
            f"故事:\n{json.dumps(story, ensure_ascii=False)}\n\n原文:\n{src[:16000]}"
        )
        raw = self.model.chat("你是短视频脚本与 AI 视频提示词专家。", prompt)
        data = json.loads(self._extract_json(raw))
        if isinstance(data, dict):
            data = data.get("scripts") or data.get("data") or [data]
        if not isinstance(data, list):
            raise ValueError("模型返回的脚本格式不正确")
        # 忽略模型给的 index，统一重排为连续 1..N，避免缺失/重复/0 起始导致列表崩溃
        scripts = []
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = len(scripts) + 1
            scripts.append(
                ScriptItem(
                    index=idx,
                    title=str(item.get("title") or f"第 {idx} 集"),
                    summary=str(item.get("summary", "")),
                    narration=str(item.get("narration", "")),
                    shots=self._normalize_shots(item.get("shots", []), seconds_per_video),
                )
            )
        if not scripts:
            raise ValueError("模型未返回任何脚本")
        return scripts

    def _normalize_shots(self, shots, seconds_per_video: int) -> list[dict]:
        if not isinstance(shots, list) or not shots:
            return [{"duration": seconds_per_video, "visual_prompt": "", "voiceover": ""}]
        default_duration = max(1, seconds_per_video // len(shots))
        normalized = []
        for shot in shots:
            if not isinstance(shot, dict):
                shot = {"voiceover": str(shot)}
            duration = shot.get("duration", default_duration)
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                duration = default_duration
            normalized.append(
                {
                    "duration": duration,
                    "visual_prompt": str(shot.get("visual_prompt", "")),
                    "voiceover": str(shot.get("voiceover", "")),
                }
            )
        return normalized

    def _scripts_locally(self, story: dict, source_text: str, seconds_per_video: int,
                         target_count: int) -> list[ScriptItem]:
        sentences = self._sentences(source_text or story["outline"])
        if not sentences:
            sentences = [story["outline"]]
        if target_count > 0:
            chunk_size = max(1, math.ceil(len(sentences) / target_count))
        else:
            chunk_size = 12
        chunks = [sentences[i : i + chunk_size] for i in range(0, len(sentences), chunk_size)]
        max_items = target_count if target_count > 0 else max(1, min(30, len(chunks)))
        scripts = []
        for idx, chunk in enumerate(chunks[:max_items], 1):
            summary = " ".join(chunk[:3])[:320]
            narration = self._build_narration(chunk)
            title = self._episode_title(chunk, idx, story["title"])
            shots = self._build_shots(chunk, seconds_per_video)
            scripts.append(ScriptItem(idx, title, summary, narration, shots))
        return scripts

    def _build_narration(self, chunk: list[str]) -> str:
        lines = []
        for i, sentence in enumerate(chunk[:10]):
            prefix = "开场钩子：" if i == 0 else "旁白："
            lines.append(f"{prefix}{sentence}")
        lines.append("结尾：这一段的变化，正把故事推向下一个关键节点。")
        return "\n".join(lines)

    def _build_shots(self, chunk: list[str], seconds_per_video: int) -> list[dict]:
        shot_count = min(8, max(4, math.ceil(len(chunk) / 2)))
        duration = max(5, seconds_per_video // shot_count)
        shots = []
        for i in range(shot_count):
            sentence = chunk[min(i * 2, len(chunk) - 1)]
            shots.append(
                {
                    "duration": duration,
                    "visual_prompt": (
                        f"电影感短视频画面，第 {i + 1} 镜，围绕“{sentence[:80]}”展开；"
                        "真实细节，清晰主体，情绪明确，适合 AI 视频生成。"
                    ),
                    "voiceover": sentence,
                }
            )
        return shots

    def _guess_title(self, text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        first = re.sub(r"^[#\s\d.、-]+", "", first)
        return first[:28] or "自动视频项目"

    def _episode_title(self, chunk: list[str], idx: int, fallback: str) -> str:
        """挑选信息量最大的句子作为核心，提炼成有概括性的本集标题。"""
        if not chunk:
            return f"第 {idx} 集 · {fallback[:16]}"
        keywords = set(self._keywords(" ".join(chunk)))

        def score(sentence: str) -> int:
            return sum(1 for word in keywords if word in sentence)

        # 关键词命中最多、长度适中的句子最能代表本段
        best = max(chunk, key=lambda s: (score(s), -abs(len(s) - 24)))
        # 在该句内挑关键词密度最高的分句，避免取到“三天后”这类时间状语
        clauses = [c.strip() for c in re.split(r"[，。！？；：,.!?;:、]", best) if c.strip()]

        def clause_key(c: str):
            return (score(c), -abs(len(c) - 10))

        phrase = max(clauses, key=clause_key) if clauses else best
        # 去掉“第N章/回/节/集”等章节标记和开头编号，让标题更像内容概括
        phrase = re.sub(r"^第?\s*[0-9一二三四五六七八九十百千]+\s*[章回节集卷部篇]\s*", "", phrase)
        phrase = re.sub(r"^[\s\d.、:：-]+", "", phrase).strip()
        phrase = phrase[:16] or fallback[:16]
        return f"第 {idx} 集 · {phrase}"

    def _sentences(self, text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", compact_text(text))
        return [part.strip() for part in parts if len(part.strip()) > 8]

    def _keywords(self, text: str) -> list[str]:
        candidates = re.findall(r"[一-龥A-Za-z0-9]{2,12}", text)
        stop = {"一个", "这个", "那个", "他们", "我们", "因为", "所以", "但是", "如果", "然后", "可以", "进行"}
        seen = []
        for word in candidates:
            if word not in stop and word not in seen:
                seen.append(word)
        return seen

    def _extract_json(self, raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()
        first_obj = raw.find("{")
        first_arr = raw.find("[")
        starts = [pos for pos in [first_obj, first_arr] if pos >= 0]
        if not starts:
            return raw
        start = min(starts)
        end = max(raw.rfind("}"), raw.rfind("]"))
        return raw[start : end + 1]
