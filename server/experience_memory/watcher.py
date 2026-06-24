from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import ExperienceStore


def session_files(root: Path) -> list[Path]:
    return sorted(root.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_session_scaffold_text(text: str) -> bool:
    stripped = text.lstrip()
    if stripped.lower() in {"auto"}:
        return True
    if stripped.startswith("# AGENTS.md instructions for ") and "<INSTRUCTIONS>" in stripped[:2000]:
        return True
    if stripped.startswith("<environment_context>"):
        return True
    return False


def content_text(value: Any) -> str:
    parts = []
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, list):
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                typ = str(part.get("type", ""))
                if typ and typ not in {"input_text", "output_text", "text"}:
                    continue
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
    elif isinstance(value, dict):
        if isinstance(value.get("text"), str):
            parts.append(value["text"])
        elif isinstance(value.get("content"), str):
            parts.append(value["content"])
    return compact_text(" ".join(parts))


def message_text(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
    typ = str(row.get("type", ""))

    if typ == "event_msg":
        event_type = str(payload.get("type", ""))
        if event_type == "user_message":
            return content_text(payload.get("message"))
        if event_type == "task_complete":
            return content_text(payload.get("last_agent_message"))
        if event_type == "agent_message" and str(payload.get("phase", "")) in {"final", "answer"}:
            return content_text(payload.get("message"))
        return ""

    if typ == "response_item":
        item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        if str(item.get("type", "")) != "message":
            return ""
        role = str(item.get("role", ""))
        if role not in {"user", "assistant"}:
            return ""
        phase = str(item.get("phase") or payload.get("phase") or "")
        if role == "assistant" and phase and phase not in {"final", "answer"}:
            return ""
        return content_text(item.get("content"))

    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if isinstance(item, dict):
        if str(item.get("type", "")) == "message" and str(item.get("role", "")) not in {"", "user", "assistant"}:
            return ""
        return content_text(item.get("content") or item.get("text") or item.get("summary") or item.get("message"))
    return ""


def parse_event_time(payload: dict[str, Any], fallback: float) -> str:
    candidates = [payload]
    if isinstance(payload.get("payload"), dict):
        candidates.append(payload["payload"])
    for item in candidates:
        for key in ("timestamp", "created_at", "time"):
            value = item.get(key)
            if isinstance(value, str) and value:
                if value.endswith("Z"):
                    return value
                try:
                    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    pass
    return datetime.fromtimestamp(fallback, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize_lines(lines: list[str], fallback_mtime: float, max_items: int = 500) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ = str(payload.get("type", ""))
        if typ not in {"response_item", "event_msg", "turn_context"} and "message" not in payload and "item" not in payload:
            continue
        text = message_text(payload)
        dedupe_key = compact_text(text).lower()[:500]
        if text and not is_session_scaffold_text(text) and dedupe_key not in seen:
            seen.add(dedupe_key)
            summaries.append({"summary": text[:500], "created_at": parse_event_time(payload, fallback_mtime)})
        if len(summaries) >= max_items:
            break
    return summaries


def summarize_session(path: Path, max_items: int = 500) -> list[dict[str, Any]]:
    try:
        mtime = path.stat().st_mtime
        return summarize_lines(path.read_text(encoding="utf-8", errors="ignore").splitlines()[-3000:], mtime, max_items)
    except FileNotFoundError:
        return []


def read_appended_lines(path: Path, offset: int) -> tuple[list[str], int, float, int]:
    stat = path.stat()
    if offset > stat.st_size:
        offset = 0
    with path.open("rb") as file:
        file.seek(offset)
        data = file.read()
    if not data:
        return [], offset, stat.st_mtime, stat.st_size
    next_offset = stat.st_size
    if not data.endswith(b"\n"):
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            return [], offset, stat.st_mtime, stat.st_size
        next_offset = offset + last_newline + 1
        data = data[: last_newline + 1]
    return data.decode("utf-8", errors="ignore").splitlines(), next_offset, stat.st_mtime, stat.st_size


def new_session_items(path: Path, checkpoint: Any, max_items: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stat = path.stat()
    if isinstance(checkpoint, dict):
        if checkpoint.get("mtime") == stat.st_mtime and checkpoint.get("size") == stat.st_size:
            return [], checkpoint
        lines, offset, mtime, size = read_appended_lines(path, int(checkpoint.get("offset", 0)))
        items = summarize_lines(lines, mtime, max_items)
        turn_index = int(checkpoint.get("turn_index", 0)) + len(items)
        return items, {"offset": offset, "mtime": mtime, "size": size, "turn_index": turn_index}

    # ponytail: one-run migration from old count checkpoint; after this, byte offsets own the stream.
    items = summarize_session(path, max_items)
    seen = int(checkpoint or 0)
    return items[seen:], {"offset": stat.st_size, "mtime": stat.st_mtime, "size": stat.st_size, "turn_index": max(seen, len(items))}


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.sessions).expanduser()
    store = ExperienceStore(Path(args.home))
    state_path = store.state_dir / "watcher.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    actions = []
    for path in session_files(root)[: args.max_files]:
        rel = str(path)
        previous = state.get(rel)
        new, checkpoint = new_session_items(path, state.get(rel), args.max_items)
        start_index = int(previous.get("turn_index", 0) if isinstance(previous, dict) else previous or 0)
        if not new:
            state[rel] = checkpoint
            continue
        for idx, item in enumerate(new, start=start_index + 1):
            store.record_turn(summary=item["summary"], conversation_id=rel, turn_index=idx, created_at=item["created_at"])
        state[rel] = checkpoint
    actions.append({"type": "dream", "result": store.dream_incremental(args.window_minutes, args.lookback_hours, args.topic, args.max_results, args.force, args.auto_apply, not args.no_ai)})
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"actions": actions, "state_path": str(state_path)}


def self_check() -> None:
    temp = Path(tempfile.mkdtemp(prefix="cem-watcher-"))
    try:
        sessions = temp / "sessions"
        sessions.mkdir()
        session = sessions / "rollout.jsonl"
        session.write_text(
            json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "first useful turn"}}) + "\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            home=str(temp / "home"),
            sessions=str(sessions),
            window_minutes=30,
            lookback_hours=24,
            topic="test",
            max_results=8,
            max_files=3,
            max_items=500,
            force=False,
            auto_apply=False,
            no_ai=True,
        )
        first = run_once(args)
        state = json.loads(Path(first["state_path"]).read_text(encoding="utf-8"))
        checkpoint = state[str(session)]
        assert checkpoint["turn_index"] == 1
        before_offset = checkpoint["offset"]
        session.write_text(
            session.read_text(encoding="utf-8")
            + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "second useful turn"}})
            + "\n",
            encoding="utf-8",
        )
        second = run_once(args)
        state = json.loads(Path(second["state_path"]).read_text(encoding="utf-8"))
        assert state[str(session)]["offset"] > before_offset
        with ExperienceStore(temp / "home").connect() as conn:
            rows = conn.execute("select turn_index, summary from turns order by id").fetchall()
        assert [(row[0], row[1]) for row in rows] == [(1, "first useful turn"), (2, "second useful turn")]
        print(json.dumps({"ok": True, "turns": len(rows)}, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(temp)


def daemonize(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        "-m",
        "experience_memory.watcher",
        "--home",
        args.home,
        "--sessions",
        args.sessions,
        "--window-minutes",
        str(args.window_minutes),
        "--lookback-hours",
        str(args.lookback_hours),
        "--max-files",
        str(args.max_files),
        "--max-items",
        str(args.max_items),
        "--interval",
        str(args.interval or args.window_minutes * 60),
    ]
    if args.auto_apply:
        cmd.append("--auto-apply")
    if args.no_ai:
        cmd.append("--no-ai")
    subprocess.Popen(cmd, cwd=Path(__file__).resolve().parents[1], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
    print(json.dumps({"started": True, "command": cmd}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sleep-time dreaming over Codex sessions.")
    parser.add_argument("--home", default="~/.codex/experience-memory")
    parser.add_argument("--sessions", default="~/.codex/sessions")
    parser.add_argument("--window-minutes", type=int, default=30)
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--topic", default="codex session lessons")
    parser.add_argument("--max-results", type=int, default=8)
    parser.add_argument("--max-files", type=int, default=3)
    parser.add_argument("--max-items", type=int, default=500)
    parser.add_argument("--interval", type=float, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--auto-apply", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if args.daemon:
        daemonize(args)
        return
    while True:
        result = run_once(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.interval <= 0:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
