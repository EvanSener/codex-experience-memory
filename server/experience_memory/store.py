from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_HOME = Path("~/.codex/experience-memory").expanduser()

TOKEN_RE = re.compile(r"`([^`]{2,160})`|[A-Za-z0-9_./:-]{3,}|[\u4e00-\u9fff]{2,}")
PATH_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] or "memory"


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def redact(text: str) -> str:
    result = text or ""
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(lambda m: m.group(1) + "=<redacted>" if m.groups() else "<redacted>", result)
    return result


def memory_quality(text: str) -> tuple[bool, str]:
    clean = text.strip()
    if len(clean) < 40:
        return False, "too short"
    for pattern in SECRET_PATTERNS:
        if pattern.search(clean):
            return False, "secret-like content"
    return True, "ok"


def extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def find_codex() -> str | None:
    found = shutil.which("codex")
    if found:
        return found
    for path in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"):
        if Path(path).exists():
            return path
    return None


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta: dict[str, Any] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
            meta[key.strip()] = items
        else:
            meta[key.strip()] = value.strip("'\"")
    return meta, text[end + 5 :]


def split_sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current = "Document"
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("#"):
            if buf:
                sections.append((current, "\n".join(buf).strip()))
                buf = []
            current = line.lstrip("#").strip() or "Section"
        else:
            buf.append(line)
    if buf:
        sections.append((current, "\n".join(buf).strip()))
    return [(h, c) for h, c in sections if c] or [("Document", body.strip())]


def extract_entities(text: str, meta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    meta = meta or {}
    found: dict[tuple[str, str], dict[str, str]] = {}

    def add(name: str, typ: str) -> None:
        clean = name.strip().strip(".,;:，。；：()[]{}<>\"'")
        if len(clean) < 2:
            return
        key = (normalize(clean), typ)
        found[key] = {"name": clean, "type": typ, "normalized": key[0]}

    for key in ("topic", "scope", "id"):
        if meta.get(key):
            add(str(meta[key]), key)
    for value in meta.get("supersedes", []) if isinstance(meta.get("supersedes"), list) else []:
        add(str(value), "memory")

    for match in PATH_RE.finditer(text):
        add(match.group(0), "path")

    for match in TOKEN_RE.finditer(text):
        value = match.group(1) or match.group(0)
        typ = "term"
        if "/" in value or "." in value:
            typ = "path"
        elif value.isupper() and len(value) > 3:
            typ = "symbol"
        elif value.startswith(("repo:", "file:", "skill:", "mcp:")):
            typ = "scope"
        add(value, typ)

    return list(found.values())[:80]


@dataclass
class ExperienceStore:
    home: Path = DEFAULT_HOME

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser()
        self.memories = self.home / "memories"
        self.index_dir = self.home / "index"
        self.state_dir = self.home / "state"
        self.cache_dir = self.home / "cache"
        self.logs_dir = self.home / "logs"
        self.archive_dir = self.home / "archive"
        self.db_path = self.index_dir / "experience.sqlite"
        for path in (self.memories, self.index_dir, self.state_dir, self.cache_dir, self.logs_dir, self.archive_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_layout()
        self._init_db()

    def _migrate_legacy_layout(self) -> None:
        moves = {
            self.home / "index.sqlite": self.db_path,
            self.home / "dream-state.json": self.state_dir / "dream.json",
            self.home / "watcher-state.json": self.state_dir / "watcher.json",
            self.home / "last-ai-summary.txt": self.cache_dir / "last-ai-summary.txt",
            self.home / "ai-summary.schema.json": self.cache_dir / "ai-summary.schema.json",
        }
        for src, dst in moves.items():
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists memory_files (
                  id integer primary key,
                  path text unique not null,
                  topic text,
                  scope text,
                  updated_at text,
                  mtime real,
                  checksum text
                );
                create table if not exists events (
                  id integer primary key,
                  file_id integer not null,
                  heading text,
                  summary text,
                  evidence text,
                  confidence real default 1.0
                );
                create table if not exists entities (
                  id integer primary key,
                  name text not null,
                  type text not null,
                  normalized text not null,
                  unique(normalized, type)
                );
                create table if not exists aliases (
                  entity_id integer not null,
                  alias text not null,
                  normalized text not null,
                  unique(entity_id, normalized)
                );
                create table if not exists relations (
                  src_type text not null,
                  src_id integer not null,
                  dst_type text not null,
                  dst_id integer not null,
                  relation text not null,
                  confidence real default 1.0,
                  unique(src_type, src_id, dst_type, dst_id, relation)
                );
                create table if not exists turns (
                  id integer primary key,
                  conversation_id text not null,
                  turn_index integer,
                  summary text,
                  raw text,
                  created_at text not null
                );
                create table if not exists patches (
                  id integer primary key,
                  status text not null,
                  proposal_json text not null,
                  created_at text not null
                );
                create virtual table if not exists event_fts
                  using fts5(event_id unindexed, text);
                """
            )

    def rebuild_index(self) -> dict[str, Any]:
        with self.connect() as conn:
            conn.executescript(
                """
                delete from event_fts;
                delete from relations;
                delete from aliases;
                delete from entities;
                delete from events;
                delete from memory_files;
                """
            )
            files = sorted(self.memories.glob("*.md"))
            event_count = 0
            entity_count = 0
            for path in files:
                event_count += self._index_file(conn, path)
            entity_count = conn.execute("select count(*) from entities").fetchone()[0]
        return {"memory_files": len(files), "events": event_count, "entities": entity_count}

    def _index_file(self, conn: sqlite3.Connection, path: Path) -> int:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rel = path.relative_to(self.home).as_posix()
        conn.execute(
            """
            insert into memory_files(path, topic, scope, updated_at, mtime, checksum)
            values (?, ?, ?, ?, ?, ?)
            """,
            (rel, meta.get("topic") or path.stem, meta.get("scope", ""), meta.get("updated_at", ""), path.stat().st_mtime, checksum),
        )
        file_id = conn.execute("select id from memory_files where path = ?", (rel,)).fetchone()[0]
        file_entities = extract_entities(text, meta)
        for entity in file_entities:
            entity_id = self._upsert_entity(conn, entity)
            self._upsert_relation(conn, "file", file_id, "entity", entity_id, "mentions", 1.0)

        count = 0
        for heading, content in split_sections(body):
            summary = " ".join(content.split())[:500]
            cur = conn.execute(
                "insert into events(file_id, heading, summary, evidence, confidence) values (?, ?, ?, ?, ?)",
                (file_id, heading, summary, content[:1200], 1.0),
            )
            event_id = cur.lastrowid
            conn.execute(
                "insert into event_fts(event_id, text) values (?, ?)",
                (event_id, f"{heading}\n{content}"),
            )
            self._upsert_relation(conn, "file", file_id, "event", event_id, "contains", 1.0)
            for entity in extract_entities(f"{heading}\n{content}", meta):
                entity_id = self._upsert_entity(conn, entity)
                self._upsert_relation(conn, "event", event_id, "entity", entity_id, "mentions", 1.0)
                self._upsert_relation(conn, "file", file_id, "entity", entity_id, "mentions", 0.8)
            count += 1
        return count

    def _upsert_entity(self, conn: sqlite3.Connection, entity: dict[str, str]) -> int:
        conn.execute(
            "insert or ignore into entities(name, type, normalized) values (?, ?, ?)",
            (entity["name"], entity["type"], entity["normalized"]),
        )
        row = conn.execute(
            "select id from entities where normalized = ? and type = ?",
            (entity["normalized"], entity["type"]),
        ).fetchone()
        return int(row[0])

    def _upsert_relation(
        self,
        conn: sqlite3.Connection,
        src_type: str,
        src_id: int,
        dst_type: str,
        dst_id: int,
        relation: str,
        confidence: float,
    ) -> None:
        conn.execute(
            """
            insert or ignore into relations(src_type, src_id, dst_type, dst_id, relation, confidence)
            values (?, ?, ?, ?, ?, ?)
            """,
            (src_type, src_id, dst_type, dst_id, relation, confidence),
        )

    def record_turn(
        self,
        summary: str,
        conversation_id: str = "default",
        turn_index: int | None = None,
        raw: str = "",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        summary = redact(summary)
        raw = redact(raw)
        created_at = created_at or utc_now()
        with self.connect() as conn:
            conn.execute(
                "insert into turns(conversation_id, turn_index, summary, raw, created_at) values (?, ?, ?, ?, ?)",
                (conversation_id, turn_index, summary, raw, created_at),
            )
            count = conn.execute(
                "select count(*) from turns where conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
        return {"conversation_id": conversation_id, "turn_count": count, "created_at": created_at}

    def recent_turns(self, conversation_id: str = "default", limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select turn_index, summary, created_at from turns
                where conversation_id = ?
                order by id desc
                limit ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def turns_since(self, since: str, until: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        until = until or utc_now()
        with self.connect() as conn:
            rows = conn.execute(
                """
                select conversation_id, turn_index, summary, created_at from turns
                where created_at > ? and created_at <= ?
                order by created_at asc, id asc
                limit ?
                """,
                (since, until, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def dream_incremental(
        self,
        window_minutes: int = 30,
        lookback_hours: int = 24,
        topic: str = "codex session lessons",
        max_results: int = 8,
        force: bool = False,
        auto_apply: bool = False,
        use_ai: bool = True,
    ) -> dict[str, Any]:
        state_path = self.state_dir / "dream.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        now = datetime.now(timezone.utc)
        last = parse_time(state.get("last_until"))
        if last and not force and now - last < timedelta(minutes=window_minutes):
            return {
                "skipped": True,
                "reason": "dream interval has not elapsed",
                "last_until": state.get("last_until"),
                "next_after": (last + timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

        floor = now - timedelta(hours=lookback_hours)
        since_dt = max(last, floor) if last else floor
        since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        turns = self.turns_since(since, until)
        draft_summary = self._summarize_turns(turns)
        if not draft_summary:
            state["last_until"] = until
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"skipped": True, "reason": "no new turns", "since": since, "until": until, "turns": []}
        ai = self._ai_summarize(turns, topic) if use_ai else None
        summary = str((ai or {}).get("lesson") or draft_summary).strip()
        ai_used = bool(ai)
        ok, reason = memory_quality(summary)
        if not ok:
            state["last_until"] = until
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"skipped": True, "reason": f"quality gate: {reason}", "since": since, "until": until, "turn_count": len(turns)}

        related = self.search_experience(summary, max_results=max_results, hops=2)
        proposal = self.propose_memory_patch(
            topic=str((ai or {}).get("topic") or topic),
            summary=f"Dream incremental window {since} -> {until}",
            problem=str((ai or {}).get("problem") or "Recent Codex work may contain reusable lessons."),
            solution=summary,
            scope=f"time-window:{since}/{until}",
            evidence=str((ai or {}).get("evidence") or "\n".join(f"- {t.get('conversation_id', '')}: {t.get('summary', '')}" for t in turns[-12:])),
            related_query=summary,
        )
        actions = []
        if not related["results"]:
            actions.append({"action": "add", "reason": "No related memory file was found for the recent session."})
        else:
            actions.append(
                {
                    "action": "update",
                    "path": related["results"][0]["path"],
                    "reason": "Top SAG match shares entities with the recent session.",
                }
            )
            for item in related["results"][1:]:
                if item["score"] >= 2:
                    actions.append({"action": "review_related", "path": item["path"], "reason": ", ".join(item["reasons"][:3])})
        state["last_until"] = until
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {
            "skipped": False,
            "since": since,
            "until": until,
            "turn_count": len(turns),
            "summary": summary,
            "ai_used": ai_used,
            "related_files": related["results"],
            "actions": actions,
            "proposal": proposal,
        }
        if auto_apply:
            result["applied"] = self.apply_memory_patch(proposal)
        return result

    def _ai_summarize(self, turns: list[dict[str, Any]], topic: str) -> dict[str, Any] | None:
        codex = find_codex()
        if not codex:
            return None
        payload = "\n".join(f"- [{t.get('created_at')}] {t.get('summary')}" for t in turns[-80:])
        prompt = (
            "你是 sleep-time memory subagent。阅读最近 Codex 对话记录，提炼可复用经验。\n"
            "只输出 JSON，不要 Markdown，不要解释。字段：topic, problem, lesson, evidence。\n"
            "规则：只保留未来会复用的经验；不要记录密钥、个人隐私、一次性闲聊；lesson 用中文，具体、可操作。\n\n"
            f"默认主题：{topic}\n\n最近记录：\n{payload}\n"
        )
        out = self.cache_dir / "last-ai-summary.txt"
        schema = self.cache_dir / "ai-summary.schema.json"
        schema.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "problem": {"type": "string"},
                        "lesson": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["topic", "problem", "lesson", "evidence"],
                    "additionalProperties": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [
                    codex,
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--output-schema",
                    str(schema),
                    "--output-last-message",
                    str(out),
                    "-",
                ],
                input=prompt,
                cwd=str(self.home),
                env=os.environ.copy(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            text = out.read_text(encoding="utf-8") if out.exists() else result.stdout
            data = extract_json(text)
            if result.returncode != 0 or not data:
                return None
            if not str(data.get("lesson", "")).strip():
                return None
            return data
        except Exception:
            return None

    def remember(
        self,
        topic: str,
        lesson: str,
        scope: str = "",
        evidence: str = "",
        apply: bool = True,
    ) -> dict[str, Any]:
        ok, reason = memory_quality(lesson)
        if not ok:
            return {"skipped": True, "reason": f"quality gate: {reason}", "topic": topic}
        proposal = self.propose_memory_patch(
            topic=topic,
            summary=f"Manual remember: {topic}",
            problem="User explicitly taught this lesson.",
            solution=lesson,
            scope=scope,
            evidence=evidence,
            related_query=f"{topic}\n{lesson}\n{scope}",
        )
        result: dict[str, Any] = {"proposal": proposal}
        if apply:
            result["applied"] = self.apply_memory_patch(proposal)
        return result

    def _summarize_turns(self, turns: list[dict[str, Any]]) -> str:
        summaries = [str(t.get("summary", "")).strip() for t in turns if str(t.get("summary", "")).strip()]
        if not summaries:
            return ""
        # ponytail: deterministic extractive summary; swap in LLM summarization when quality matters.
        return "\n".join(f"- {line[:500]}" for line in summaries[-12:])

    def search_experience(self, query: str, max_results: int = 5, hops: int = 2) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {"query": query, "results": []}
        self.rebuild_index()
        query_entities = extract_entities(query)
        scores: dict[int, float] = {}
        reasons: dict[int, set[str]] = {}
        with self.connect() as conn:
            for row in self._fts_rows(conn, query, max_results * 4):
                file_id = int(row["file_id"])
                scores[file_id] = scores.get(file_id, 0) + float(row["rank_score"])
                reasons.setdefault(file_id, set()).add(f"fts:{row['heading']}")

            norms = [entity["normalized"] for entity in query_entities]
            if norms:
                placeholders = ",".join("?" for _ in norms)
                rows = conn.execute(
                    f"""
                    select mf.id file_id, mf.path, e.name, e.type, count(*) hits
                    from entities e
                    join relations r on r.dst_type = 'entity' and r.dst_id = e.id
                    join memory_files mf on r.src_type = 'file' and r.src_id = mf.id
                    where e.normalized in ({placeholders})
                    group by mf.id, e.id
                    """,
                    norms,
                ).fetchall()
                for row in rows:
                    file_id = int(row["file_id"])
                    scores[file_id] = scores.get(file_id, 0) + 3 + float(row["hits"])
                    reasons.setdefault(file_id, set()).add(f"entity:{row['name']}")

            seed_ids = sorted(scores, key=scores.get, reverse=True)[:max_results * 3]
            for hop in range(max(0, hops - 1)):
                if not seed_ids:
                    break
                expanded = self._expand_related_files(conn, seed_ids)
                for file_id, reason in expanded:
                    scores[file_id] = scores.get(file_id, 0) + max(0.5, 1.5 - hop * 0.5)
                    reasons.setdefault(file_id, set()).add(reason)
                seed_ids = [file_id for file_id, _ in expanded]

            results = self._format_results(conn, scores, reasons, max_results)
        return {
            "query": query,
            "query_entities": query_entities,
            "results": results,
        }

    def _fts_rows(self, conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
        terms = [t["normalized"] for t in extract_entities(query)]
        terms = [term.replace('"', "") for term in terms if len(term) >= 2][:8]
        if not terms:
            terms = [normalize(part) for part in re.findall(r"\w{3,}", query)[:8]]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        try:
            return conn.execute(
                """
                select ev.file_id, ev.heading, ev.summary, bm25(event_fts) * -1 rank_score
                from event_fts
                join events ev on ev.id = event_fts.event_id
                where event_fts match ?
                order by rank_score desc
                limit ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.Error:
            like = f"%{query[:80]}%"
            return conn.execute(
                """
                select ev.file_id, ev.heading, ev.summary, 1.0 rank_score
                from events ev
                where ev.summary like ? or ev.evidence like ?
                limit ?
                """,
                (like, like, limit),
            ).fetchall()

    def _expand_related_files(self, conn: sqlite3.Connection, file_ids: list[int]) -> list[tuple[int, str]]:
        placeholders = ",".join("?" for _ in file_ids)
        rows = conn.execute(
            f"""
            select distinct mf2.id file_id, e.name entity_name
            from relations r1
            join entities e on r1.dst_type = 'entity' and r1.dst_id = e.id
            join relations r2 on r2.dst_type = 'entity' and r2.dst_id = e.id
            join memory_files mf2 on r2.src_type = 'file' and r2.src_id = mf2.id
            where r1.src_type = 'file'
              and r1.src_id in ({placeholders})
              and mf2.id not in ({placeholders})
            limit 40
            """,
            [*file_ids, *file_ids],
        ).fetchall()
        return [(int(row["file_id"]), f"related:{row['entity_name']}") for row in rows]

    def _format_results(
        self,
        conn: sqlite3.Connection,
        scores: dict[int, float],
        reasons: dict[int, set[str]],
        max_results: int,
    ) -> list[dict[str, Any]]:
        if not scores:
            return []
        ids = sorted(scores, key=scores.get, reverse=True)[:max_results]
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"select * from memory_files where id in ({placeholders})",
            ids,
        ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        results = []
        for file_id in ids:
            row = by_id[file_id]
            events = conn.execute(
                "select heading, summary from events where file_id = ? limit 3",
                (file_id,),
            ).fetchall()
            results.append(
                {
                    "path": row["path"],
                    "topic": row["topic"],
                    "scope": row["scope"],
                    "score": round(scores[file_id], 3),
                    "reasons": sorted(reasons.get(file_id, []))[:8],
                    "snippets": [dict(event) for event in events],
                }
            )
        return results

    def impact_analysis(self, change_summary: str, target_paths: list[str] | None = None, max_results: int = 8) -> dict[str, Any]:
        target_paths = target_paths or []
        target_text = "\n".join(target_paths)
        query = f"{change_summary}\n{target_text}".strip()
        search = self.search_experience(query, max_results=max_results, hops=2)
        target_set = set(target_paths)
        impacted = [r for r in search["results"] if r["path"] not in target_set]
        return {
            "change_summary": change_summary,
            "target_paths": target_paths,
            "changed_entities": search["query_entities"],
            "impacted_files": impacted,
        }

    def propose_memory_patch(
        self,
        topic: str,
        summary: str,
        problem: str = "",
        solution: str = "",
        scope: str = "",
        evidence: str = "",
        related_query: str = "",
        operation: str = "",
        path: str = "",
    ) -> dict[str, Any]:
        topic = topic.strip() or "experience"
        summary = redact(summary)
        problem = redact(problem)
        solution = redact(solution)
        evidence = redact(evidence)
        query = related_query or "\n".join([topic, summary, problem, solution, scope])
        related = self.search_experience(query, max_results=5, hops=2)
        if operation:
            op = operation
        elif related["results"]:
            op = "update"
        else:
            op = "add"
        if path:
            target_path = path
        elif related["results"] and op != "add":
            target_path = related["results"][0]["path"]
        else:
            target_path = f"memories/{slugify(topic)}.md"
        proposal = {
            "operation": op,
            "path": target_path,
            "topic": topic,
            "scope": scope,
            "summary": summary,
            "problem": problem,
            "solution": solution,
            "evidence": evidence,
            "related_files": related["results"],
            "created_at": utc_now(),
        }
        with self.connect() as conn:
            cur = conn.execute(
                "insert into patches(status, proposal_json, created_at) values (?, ?, ?)",
                ("proposed", json.dumps(proposal, ensure_ascii=False), utc_now()),
            )
            proposal["patch_id"] = cur.lastrowid
        return proposal

    def apply_memory_patch(self, proposal: dict[str, Any] | int, commit: bool = True) -> dict[str, Any]:
        if isinstance(proposal, int):
            with self.connect() as conn:
                row = conn.execute("select proposal_json from patches where id = ?", (proposal,)).fetchone()
            if not row:
                raise ValueError(f"patch not found: {proposal}")
            proposal_data = json.loads(row["proposal_json"])
            patch_id = proposal
        else:
            proposal_data = proposal
            patch_id = proposal_data.get("patch_id")

        rel = proposal_data["path"]
        if not rel.startswith("memories/") or ".." in Path(rel).parts:
            raise ValueError("patch path must stay under memories/")
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        operation = proposal_data.get("operation")
        if operation not in {"add", "update", "delete", "supersede"}:
            raise ValueError(f"unsupported patch operation: {operation}")
        if operation == "delete":
            if path.exists():
                archived = self.archive_dir / f"{path.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
                path.rename(archived)
            result_path = rel
        elif operation == "supersede":
            if not path.exists():
                raise ValueError(f"cannot supersede missing memory: {rel}")
            existing = path.read_text(encoding="utf-8")
            marker = f"\n\n## Superseded {now}\n\n{proposal_data.get('summary', '').strip()}\n"
            path.write_text(existing.rstrip() + marker, encoding="utf-8")
            result_path = rel
        elif operation == "add" or not path.exists():
            content = self._new_memory_content(proposal_data, now)
            path.write_text(content, encoding="utf-8")
            result_path = rel
        else:
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing.rstrip() + "\n\n" + self._append_section(proposal_data, now), encoding="utf-8")
            result_path = rel

        stats = self.rebuild_index()
        commit_result = self._git_commit(f"Update experience memory: {proposal_data.get('topic', path.stem)}") if commit else "skipped"
        if patch_id:
            with self.connect() as conn:
                conn.execute("update patches set status = ? where id = ?", ("applied", patch_id))
        return {"path": result_path, "operation": operation, "index": stats, "git": commit_result}

    def _new_memory_content(self, proposal: dict[str, Any], now: str) -> str:
        topic = proposal.get("topic") or Path(proposal["path"]).stem
        scope = proposal.get("scope") or ""
        body = self._append_section(proposal, now)
        return (
            "---\n"
            f"id: {slugify(topic)}\n"
            f"topic: {topic}\n"
            f"scope: {scope}\n"
            f"updated_at: {now}\n"
            "supersedes: []\n"
            "---\n\n"
            f"# {topic}\n\n"
            f"{body}"
        )

    def _append_section(self, proposal: dict[str, Any], now: str) -> str:
        lines = [f"## {now} {proposal.get('summary', '').strip()[:80]}".rstrip(), ""]
        if proposal.get("problem"):
            lines += ["### 常见问题", proposal["problem"].strip(), ""]
        if proposal.get("solution"):
            lines += ["### 解决方案", proposal["solution"].strip(), ""]
        if proposal.get("evidence"):
            lines += ["### 来源证据", proposal["evidence"].strip(), ""]
        lines += ["### 关联提示", "后续编辑前先运行 impact_analysis，检查同实体关联的经验文件。", ""]
        return "\n".join(lines)

    def _git_commit(self, message: str) -> str:
        if not shutil.which("git"):
            return "git not found"
        try:
            if not (self.home / ".git").exists():
                subprocess.run(["git", "init"], cwd=self.home, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                subprocess.run(["git", "config", "user.name", "Codex Experience Memory"], cwd=self.home, check=True)
                subprocess.run(["git", "config", "user.email", "codex-experience-memory@local"], cwd=self.home, check=True)
            subprocess.run(["git", "add", "memories"], cwd=self.home, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.home)
            if diff.returncode == 0:
                return "no changes"
            subprocess.run(["git", "commit", "-m", message], cwd=self.home, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return "committed"
        except Exception as exc:  # pragma: no cover - git availability differs by machine.
            return f"git skipped: {exc}"
