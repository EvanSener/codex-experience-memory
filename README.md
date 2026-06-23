# Codex Experience Memory

Local Codex plugin for automatic experience memory.

## Install From GitHub

Ask Codex to do the install:

```text
请从 https://github.com/EvanSener/codex-experience-memory.git 安装 Codex Experience Memory 插件。
请先运行 codex plugin marketplace add EvanSener/codex-experience-memory，
再运行 codex plugin add codex-experience-memory@codex-experience-memory。
安装后请运行插件自检，并安装 30 分钟 launchd 后台 dreamer。
```

Manual install:

```bash
codex plugin marketplace add EvanSener/codex-experience-memory
codex plugin add codex-experience-memory@codex-experience-memory
```

For the macOS background dreamer, clone into the personal marketplace layout and install launchd:

```bash
mkdir -p ~/plugins
git clone https://github.com/EvanSener/codex-experience-memory.git ~/plugins/codex-experience-memory
codex plugin marketplace add ~
codex plugin add codex-experience-memory@personal
~/plugins/codex-experience-memory/scripts/install-launchd.sh
```

## Layout

```text
~/.codex/experience-memory/
  memories/              # Markdown source of truth, git-tracked
  index/experience.sqlite # rebuildable SAG index
  state/                 # dream and watcher checkpoints
  cache/                 # last AI summary and schema
  logs/                  # launchd output
  archive/               # deleted memory files
```

## Tools

- `search_experience`: SAG search over Markdown memories.
- `impact_analysis`: related-file check before memory edits.
- `remember`: manual `/remember`, auto-applies by default.
- `dream_incremental`: summarize new turns and write lessons.
- `memory_status`: show paths, counts, and recent turns.

## Background Dreaming

Install the 30-minute macOS hard trigger:

```bash
~/plugins/codex-experience-memory/scripts/install-launchd.sh
```

The watcher scans `~/.codex/sessions`, records compact user/final assistant messages, then calls
`dream_incremental --auto-apply`. AI summarization runs in a separate native Codex session via
`codex exec --ephemeral`, using the normal Codex login and config.

Uninstall:

```bash
~/plugins/codex-experience-memory/scripts/uninstall-launchd.sh
```

## Checks

```bash
cd ~/plugins/codex-experience-memory/server
python3 -m experience_memory.cli demo
python3 -m experience_memory.cli status
python3 -m experience_memory.watcher --sessions ~/.codex/sessions --force --auto-apply
```

Update the installed Codex plugin after edits:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py ~/plugins/codex-experience-memory
codex plugin add codex-experience-memory@personal
```
