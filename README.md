# Prosewrite

An AI book writing pipeline — multi-stage, human-in-the-loop, personality-rich prose.

Prosewrite runs your novel through seven sequential stages, each with its own AI call, its own temperature, and its own approval gate. Nothing advances without your sign-off.

---

## Quick start

```bash
# Create and activate a virtualenv
python -m venv .venv && source .venv/bin/activate

# Install
pip install -e .

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Write a seed file describing your book idea (plain prose, a page or two)
echo "A rogue signals analyst discovers the casualty figures from a classified..." > seed.md

# Start your first project
prosewrite new --name unit_985 --seed seed.md

# Resume after closing the terminal
prosewrite resume --name unit_985
```

---

## Pipeline stages

| # | Stage | What it produces |
|---|-------|-----------------|
| 0 | Seed Analysis | Critical editorial review of your premise |
| 1 | Story Bible | Genre, arc, themes, chapter count — the north star |
| 2 | World Builder | Settings, locations, world rules, glossary |
| 3 | Characters | Character index + per-character voice profiles |
| 4 | Chapter Outlines | Chapter list + detailed per-chapter outlines |
| 5 | Chapter Writing | Draft → AI review → revision → approval |
| 6 | Export | `manuscript.md` (+ optional `.docx`) |

At every stage you can:
- **Approve** — save and move on (`yes`, `good`, `approve`, `next`, etc.)
- **Regenerate** — throw it away and try again (`redo`, `again`, `try again`)
- **Give feedback** — type your notes and the model revises
- **Edit yourself** — paste your own version (`i'll edit it`)
- **Use review** — regenerate using the AI reviewer's brief (`use review`)
- **Skip** — skip this item (chapter stages only) (`skip`)

---

## Configuration

Edit `config.toml` to control every pipeline stage. The file ships with sensible defaults.

### Per-stage model and temperature

```toml
[stages.chapter_writer]
api_base_url = "https://api.anthropic.com/v1"
model        = "claude-opus-4-5"
temperature  = 0.85
max_tokens   = 8000

[stages.chapter_reviewer]
model        = "claude-sonnet-4-5"
temperature  = 0.2
```

### Routing stages to a local model

```toml
[stages.chapter_writer]
api_base_url = "http://localhost:11434/v1"
api_key_env  = "OLLAMA_API_KEY"   # Ollama ignores this but the client requires a key field
model        = "llama3:70b-instruct"
temperature  = 0.9
max_tokens   = 8000
```

### Persona (fights generic output)

```toml
[persona]
name        = "Evelyn Cross"
description = """
You are Evelyn Cross, a veteran editor with 30 years in commercial fiction...
"""
```

### Style settings

```toml
[style]
pov     = "third person limited"
tense   = "past"
genre   = "military thriller"
notes   = "Short sentences in action. One precise word beats three vague ones."
```

---

## CLI reference

```bash
# Start a new project
prosewrite new --name <name> --seed <seed.md>

# Resume an existing project
prosewrite resume --name <name>

# Rerun a specific stage
prosewrite run --name <name> --stage characters

# List all projects
prosewrite list

# Export to manuscript
prosewrite export --name <name>

# Validate config
prosewrite config check

# Use a custom config file for any command
prosewrite --config /path/to/config.toml resume --name <name>
```

---

## Project files

All artefacts live in `projects/<name>/`:

```
projects/unit_985/
├── project_state.json     ← pipeline state (auto-managed)
├── seed.md                ← your original premise
├── seed_analysis.md
├── story_bible.md
├── world.md
├── character_index.md
├── characters/
│   └── <Name>.md
├── chapter_outlines/
│   ├── chapter_list.md
│   └── chapter_<N>_outline.md
├── chapters/
│   └── chapter_<N>.md
├── summaries/
│   └── macro.md           ← rolling story summary (auto-managed)
└── manuscript.md
```

All files are plain Markdown — edit them directly at any point.

---

## Customising prompts

All prompts live in `prosewrite/prompts/`. Edit them freely:

| File | Stage |
|------|-------|
| `system_persona.txt` | Injected into every system prompt |
| `stage0.txt` | Seed analysis |
| `stage1.txt` | Story bible |
| `stage2.txt` | World builder |
| `stage3.txt` | Character index / profiles (uses `stage3_index_task.txt`, `stage3_profile_task.txt`) |
| `stage4.txt` | Chapter outlines (uses `stage4_list_task.txt`, `stage4_outline_task.txt`) |
| `stage5_writer.txt` | Chapter writer |
| `stage5_reviewer.txt` | Chapter reviewer (12-criterion rubric) |
| `stage5_scene_brief.txt` | Internal scene brief (not shown to user) |
| `stage5_revision.txt` | Revision pass |

Placeholders use `[[double_brackets]]` — no escaping required around JSON or code in prompts.

---

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

---

## Troubleshooting

**`ANTHROPIC_API_KEY is not set`** — Run `export ANTHROPIC_API_KEY=sk-ant-...` before starting.

**`Config file not found`** — Run commands from the repo root, or pass `--config /path/to/config.toml`.

**`seed_analysis.md is missing`** — You must complete each stage before the next can run. Use `prosewrite resume` to pick up where you left off.

**`Could not parse character names from index`** — The model produced a non-standard Markdown table. Approve, then manually confirm the character list when prompted.

**`LLM API returned 429`** — Rate limited. Wait a moment and resume — state is saved after every approval.

**Timeout errors** — Increase `timeout_s` in `config.toml` or reduce `max_tokens` for that stage.
