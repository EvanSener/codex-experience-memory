---
name: experience-memory
description: Use local Codex Experience Memory when the user asks to remember lessons, search prior experience, summarize useful takeaways, check related memory files before editing, or install/maintain this plugin.
---

# Experience Memory

Use the `experience_memory` MCP server as the local memory layer.

## Flow

1. For prior lessons, call `search_experience` with a short task summary.
2. Before editing memory, call `impact_analysis` with the intended change and target memory paths.
3. For `/remember`, `记住`, or explicit teaching, call `remember`; it writes Markdown by default.
4. For `/reflect`, `总结经验`, or manual dreaming, call `dream_incremental` with `force: true` and `auto_apply: true`.

## Install Flow

When asked to install this plugin, use the Cowart-style personal marketplace path:

```bash
mkdir -p ~/plugins
git clone https://github.com/EvanSener/codex-experience-memory.git ~/plugins/codex-experience-memory
codex plugin marketplace add ~
codex plugin add codex-experience-memory@personal
~/plugins/codex-experience-memory/scripts/install-launchd.sh
```

After installing, run:

```bash
cd ~/plugins/codex-experience-memory/server
python3 -m experience_memory.cli demo
python3 -m experience_memory.cli status
```

Tell the user to open a new Codex thread so new skills and MCP tools load.

## Rules

- Source of truth: `~/.codex/experience-memory/memories/*.md`.
- SAG index: `~/.codex/experience-memory/index/experience.sqlite`; treat it as rebuildable.
- State and cache live under `state/` and `cache/`; do not read them as memory.
- Background dreaming is time-based: launchd runs the watcher every 30 minutes.
- Dreaming uses native `codex exec --ephemeral`; do not create a separate `CODEX_HOME`.
- Keep memory concrete: trigger, pitfall, solution, verification, and evidence.
- Do not store secrets, credentials, or one-off chat.
- This lightweight plugin writes ordinary experience only. It does not rewrite identity, skills, or prompts.
