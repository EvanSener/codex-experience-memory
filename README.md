# Codex Experience Memory

Codex Experience Memory 是一个面向 Codex 的本地长期经验记忆插件。它把可复用 lesson 写成 Markdown，用 SQLite 做 SAG 关联检索，并用 macOS launchd 每 30 分钟后台回看最近 Codex session 做 sleep-time dreaming。

## 功能

- 手动 `/remember`：把明确经验写入 Markdown memory。
- 自动 dreaming：每 30 分钟按 session 文件更新时间扫描 `~/.codex/sessions`，只读取追加内容，并用 `codex exec --ephemeral` 单开 AI 会话总结经验。
- SAG 检索：用 FTS + entity/relation 表召回相关 memory 文件。
- 联动检查：写 memory 前用 `impact_analysis` 找可能需要一起更新的文件。
- Git-backed MemFS：只跟踪 `memories/*.md`，索引和 state 都可重建。

## 让 Codex 自动安装

把下面这段发给 Codex：

```text
请从 https://github.com/EvanSener/codex-experience-memory.git 安装 Codex Experience Memory 插件。
请 clone 仓库到 ~/plugins/codex-experience-memory，确认 .codex-plugin/plugin.json 存在，
把插件加入 personal marketplace，先运行 codex plugin marketplace add ~，
再运行 codex plugin add codex-experience-memory@personal。
安装后请运行插件自检，安装 30 分钟 launchd 后台 dreamer，
并告诉我是否需要开启一个新对话来加载新技能和 MCP 工具。
```

## 手动安装

推荐把插件 clone 到 Codex personal marketplace 默认会引用的位置：

```bash
mkdir -p ~/plugins
git clone https://github.com/EvanSener/codex-experience-memory.git ~/plugins/codex-experience-memory
cd ~/plugins/codex-experience-memory
```

确保 `~/.agents/plugins/marketplace.json` 中有 Codex Experience Memory 条目：

```json
{
  "name": "personal",
  "interface": {
    "displayName": "Personal"
  },
  "plugins": [
    {
      "name": "codex-experience-memory",
      "source": {
        "source": "local",
        "path": "./plugins/codex-experience-memory"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Developer Tools"
    }
  ]
}
```

然后先注册 personal marketplace，再安装插件：

```bash
codex plugin marketplace add ~
codex plugin add codex-experience-memory@personal
```

安装 30 分钟后台 dreamer：

```bash
~/plugins/codex-experience-memory/scripts/install-launchd.sh
```

安装后建议开启一个新的 Codex 对话，让新的 skill 和 MCP 工具完整加载。

## 输出目录

```text
~/.codex/experience-memory/
  memories/               # Markdown source of truth, git-tracked
  index/experience.sqlite # rebuildable SAG index
  state/                  # dream and watcher checkpoints, including session offsets
  cache/                  # last AI summary and schema
  logs/                   # launchd output
  archive/                # deleted memory files
```

## MCP 工具

- `search_experience`：通过 SAG 搜索 Markdown memories。
- `impact_analysis`：写 memory 前检查相关文件。
- `remember`：手动 `/remember`，默认自动写入。
- `dream_incremental`：总结新增 turns 并写 lesson。
- `memory_status`：显示路径、计数和近期 turns。

## 自检

```bash
cd ~/plugins/codex-experience-memory/server
python3 -m experience_memory.cli demo
python3 -m experience_memory.cli status
python3 -m experience_memory.watcher --sessions ~/.codex/sessions --force --auto-apply
```

卸载后台 dreamer：

```bash
~/plugins/codex-experience-memory/scripts/uninstall-launchd.sh
```
