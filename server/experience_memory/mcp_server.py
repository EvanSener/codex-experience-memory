from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from .store import ExperienceStore


def store() -> ExperienceStore:
    return ExperienceStore(Path(os.environ.get("CEM_HOME", "~/.codex/experience-memory")))


def tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_experience",
            "description": "Read memory through the SAG index and return related Markdown experience files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                    "hops": {"type": "integer", "default": 2},
                },
                "required": ["query"],
            },
        },
        {
            "name": "dream_incremental",
            "description": "Sleep-time memory pass: summarize new turns since the last run within a recent time window and propose linked memory updates.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "window_minutes": {"type": "integer", "default": 30},
                    "lookback_hours": {"type": "integer", "default": 24},
                    "topic": {"type": "string", "default": "codex session lessons"},
                    "max_results": {"type": "integer", "default": 8},
                    "force": {"type": "boolean", "default": False},
                    "auto_apply": {"type": "boolean", "default": False},
                    "use_ai": {"type": "boolean", "default": True},
                },
            },
        },
        {
            "name": "remember",
            "description": "Manually teach memory, like /remember. Writes ordinary experience memory by default.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "lesson": {"type": "string"},
                    "scope": {"type": "string", "default": ""},
                    "evidence": {"type": "string", "default": ""},
                    "apply": {"type": "boolean", "default": True},
                },
                "required": ["topic", "lesson"],
            },
        },
        {
            "name": "impact_analysis",
            "description": "Before writing memory, find related files that may need linked edits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "change_summary": {"type": "string"},
                    "target_paths": {"type": "array", "items": {"type": "string"}, "default": []},
                    "max_results": {"type": "integer", "default": 8},
                },
                "required": ["change_summary"],
            },
        },
        {
            "name": "memory_status",
            "description": "Return memory home, counts, and recent turn summaries.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string", "default": "default"},
                    "recent_limit": {"type": "integer", "default": 5},
                },
            },
        },
    ]


def call_tool(name: str, args: dict[str, Any]) -> Any:
    s = store()
    tools: dict[str, Callable[[], Any]] = {
        "search_experience": lambda: s.search_experience(**args),
        "dream_incremental": lambda: s.dream_incremental(**args),
        "remember": lambda: s.remember(**args),
        "impact_analysis": lambda: s.impact_analysis(**args),
        "memory_status": lambda: {
            "home": str(s.home),
            "paths": {
                "memories": str(s.memories),
                "index": str(s.db_path),
                "state": str(s.state_dir),
                "cache": str(s.cache_dir),
                "logs": str(s.logs_dir),
            },
            "counts": s.rebuild_index(),
            "recent_turns": s.recent_turns(args.get("conversation_id", "default"), args.get("recent_limit", 5)),
        },
    }
    if name not in tools:
        raise ValueError(f"unknown tool: {name}")
    return tools[name]()


def respond(message_id: Any, result: Any = None, error: Any = None) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            method = msg.get("method")
            message_id = msg.get("id")
            if method == "initialize":
                respond(
                    message_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "experience_memory", "version": "0.1.0"},
                    },
                )
            elif method == "tools/list":
                respond(message_id, {"tools": tool_schema()})
            elif method == "tools/call":
                params = msg.get("params", {})
                result = call_tool(params.get("name", ""), params.get("arguments", {}) or {})
                respond(
                    message_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, indent=2),
                            }
                        ]
                    },
                )
            elif method and method.startswith("notifications/"):
                continue
            elif method == "ping":
                respond(message_id, {})
            else:
                respond(message_id, error={"code": -32601, "message": f"method not found: {method}"})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            respond(msg.get("id") if "msg" in locals() else None, error={"code": -32000, "message": str(exc)})


if __name__ == "__main__":
    main()
