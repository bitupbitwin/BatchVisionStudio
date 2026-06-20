"""项目任务状态：记录每集制作进度，支持断点续跑与批量管理。

状态存于项目目录下 jobs.json；镜头级断点续跑由 pipeline 的 work 清单负责。
"""

import json
from datetime import datetime
from pathlib import Path


class ProjectJobs:
    def __init__(self, project_dir):
        self.path = Path(project_dir) / "jobs.json"
        self.data = {"episodes": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("episodes"), dict):
                    self.data = loaded
            except json.JSONDecodeError:
                pass

    def status(self, index) -> str:
        return self.data["episodes"].get(str(index), {}).get("status", "pending")

    def get(self, index) -> dict:
        return self.data["episodes"].get(str(index), {})

    def set(self, index, **fields) -> None:
        ep = self.data["episodes"].setdefault(str(index), {})
        ep.update(fields)
        ep["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def all(self) -> dict:
        return self.data["episodes"]

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
