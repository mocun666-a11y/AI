"""Local, date-scoped memo data. No network services."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path

DEFAULT_SETTINGS = {"opacity": 72, "width": 400, "height": 660,
                    "calendar_mode": "date", "wallpaper": ""}
MIN_WIDTH, MIN_HEIGHT = 220, 220


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 1, "settings": DEFAULT_SETTINGS.copy(), "days": {}}
        self.recovery_message = ""
        if self.path.exists():
            try:
                self.data = self._read(self.path)
            except (ValueError, OSError, TypeError, KeyError):
                damaged = self.path.with_name(f"data-damaged-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
                shutil.copy2(self.path, damaged)
                backup = self.path.with_suffix(".backup.json")
                try:
                    self.data = self._read(backup)
                    self.recovery_message = "数据文件异常，已从上一次备份恢复。原文件已保留。"
                except (ValueError, OSError, TypeError, KeyError):
                    self.recovery_message = "数据文件异常，原文件已保留，请通过托盘菜单打开数据文件夹检查。"
                # Keep a damaged primary from replacing a valid backup on the next save.
                self._write_atomic(self.data)

    @staticmethod
    def _read(path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unrecognized data format")
        if not isinstance(data["settings"], dict) or not isinstance(data["days"], dict):
            raise ValueError("Invalid memo data")
        for day, tasks in data["days"].items():
            date.fromisoformat(day)
            if not isinstance(tasks, list):
                raise ValueError("Invalid task list")
            for task in tasks:
                if not isinstance(task["text"], str) or not isinstance(task["id"], str):
                    raise ValueError("Invalid task")
                if not isinstance(task["done"], bool):
                    raise ValueError("Invalid completion state")
        data["settings"] = {**DEFAULT_SETTINGS, **data["settings"]}
        for key, lo, hi in (("opacity", 25, 95), ("width", MIN_WIDTH, 800), ("height", MIN_HEIGHT, 1100)):
            data["settings"][key] = max(lo, min(hi, int(data["settings"][key])))
        if data["settings"]["calendar_mode"] not in ("date", "week"):
            data["settings"]["calendar_mode"] = "date"
        if not isinstance(data["settings"]["wallpaper"], str):
            data["settings"]["wallpaper"] = ""
        return data

    def _write_atomic(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def save(self):
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(".backup.json"))
        self._write_atomic(self.data)

    def tasks(self, day=None):
        return self.data["days"].get(day or date.today().isoformat(), [])

    def add(self, text, time="", day=None):
        text = text.strip()
        if not text:
            raise ValueError("请先写下计划。")
        if len(text) > 300:
            raise ValueError("每条计划最多 300 个字。")
        time = time.strip().replace("：", ":")
        if time:
            try:
                time = datetime.strptime(time, "%H:%M").strftime("%H:%M")
            except ValueError:
                raise ValueError("时间请填写 09:30 这样的格式，也可以留空。") from None
        task = {"id": uuid.uuid4().hex, "text": text, "time": time, "done": False}
        self.data["days"].setdefault(day or date.today().isoformat(), []).append(task)
        return task

    def toggle(self, task_id, day=None):
        for task in self.tasks(day):
            if task["id"] == task_id:
                task["done"] = not task["done"]
                return
        raise KeyError(task_id)

    def delete(self, task_id, day=None):
        tasks = self.tasks(day)
        for index, task in enumerate(tasks):
            if task["id"] == task_id:
                return index, tasks.pop(index)
        raise KeyError(task_id)
