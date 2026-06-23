from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from .store import ExperienceStore


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_demo(_: argparse.Namespace) -> None:
    temp = Path(tempfile.mkdtemp(prefix="cem-demo-"))
    try:
        s = ExperienceStore(temp)
        (s.memories / "codex-plugin.md").write_text(
            """---
id: codex-plugin
topic: codex plugin
scope: repo:codex-experience-memory
updated_at: 2026-06-23T00:00:00Z
supersedes: []
---

# Codex plugin

## MCP 配置
Codex plugin 通过 `.mcp.json` 暴露 experience_memory MCP server。编辑插件清单时需要同步检查 Skill 说明。
""",
            encoding="utf-8",
        )
        (s.memories / "sag-index.md").write_text(
            """---
id: sag-index
topic: SAG index
scope: repo:codex-experience-memory
updated_at: 2026-06-23T00:00:00Z
supersedes: []
---

# SAG index

## 联动编辑
SAG index 记录 memory_files、entities 和 relations。修改 `.mcp.json` 时，要联动检查 codex plugin 经验。
""",
            encoding="utf-8",
        )
        stats = s.rebuild_index()
        search = s.search_experience("修改 .mcp.json 的 Codex plugin 经验", max_results=4)
        impact = s.impact_analysis("准备修改 .mcp.json 和 experience_memory MCP server", ["memories/codex-plugin.md"])
        assert stats["memory_files"] == 2
        assert any(r["path"] == "memories/codex-plugin.md" for r in search["results"])
        assert any(r["path"] == "memories/sag-index.md" for r in impact["impacted_files"])
        print_json({"ok": True, "stats": stats, "search": search["results"], "impact": impact["impacted_files"]})
    finally:
        shutil.rmtree(temp)


def cmd_rebuild(args: argparse.Namespace) -> None:
    print_json(ExperienceStore(Path(args.home)).rebuild_index())


def cmd_search(args: argparse.Namespace) -> None:
    print_json(ExperienceStore(Path(args.home)).search_experience(args.query, args.max_results, args.hops))


def cmd_impact(args: argparse.Namespace) -> None:
    print_json(ExperienceStore(Path(args.home)).impact_analysis(args.change_summary, args.target, args.max_results))


def cmd_dream(args: argparse.Namespace) -> None:
    print_json(ExperienceStore(Path(args.home)).dream_incremental(args.window_minutes, args.lookback_hours, args.topic, args.max_results, args.force, args.auto_apply, not args.no_ai))


def cmd_remember(args: argparse.Namespace) -> None:
    print_json(ExperienceStore(Path(args.home)).remember(args.topic, args.lesson, args.scope, args.evidence, args.apply))


def cmd_status(args: argparse.Namespace) -> None:
    store = ExperienceStore(Path(args.home))
    print_json(
        {
            "home": str(store.home),
            "memories": str(store.memories),
            "index": str(store.db_path),
            "state": str(store.state_dir),
            "cache": str(store.cache_dir),
            "counts": store.rebuild_index(),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex Experience Memory")
    parser.add_argument("--home", default="~/.codex/experience-memory")
    sub = parser.add_subparsers(required=True)

    demo = sub.add_parser("demo", help="Run a temp-dir self-check.")
    demo.set_defaults(func=cmd_demo)

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--max-results", type=int, default=5)
    search.add_argument("--hops", type=int, default=2)
    search.set_defaults(func=cmd_search)

    impact = sub.add_parser("impact")
    impact.add_argument("change_summary")
    impact.add_argument("--target", action="append", default=[])
    impact.add_argument("--max-results", type=int, default=8)
    impact.set_defaults(func=cmd_impact)

    dream = sub.add_parser("dream")
    dream.add_argument("--window-minutes", type=int, default=30)
    dream.add_argument("--lookback-hours", type=int, default=24)
    dream.add_argument("--topic", default="codex session lessons")
    dream.add_argument("--max-results", type=int, default=8)
    dream.add_argument("--force", action="store_true")
    dream.add_argument("--auto-apply", action="store_true")
    dream.add_argument("--no-ai", action="store_true")
    dream.set_defaults(func=cmd_dream)

    remember = sub.add_parser("remember")
    remember.add_argument("topic")
    remember.add_argument("lesson")
    remember.add_argument("--scope", default="")
    remember.add_argument("--evidence", default="")
    remember.add_argument("--no-apply", dest="apply", action="store_false")
    remember.set_defaults(apply=True)
    remember.set_defaults(func=cmd_remember)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
