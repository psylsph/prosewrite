# Prosewrite Agent Guide

## Pipeline Architecture

Multi-stage AI book writing pipeline with human-in-the-loop approval. Stages run sequentially; state is persisted after every approval, making sessions fully resumable.

**Stage order:** `stage0_seed` → `stage1_bible` → `stage2_world` → `stage3_characters` → `stage4_outlines` → `stage5_chapters` → `stage6_batch_review` → `stage7_export`

Each stage has its own model/temperature config block in `config.toml`. Stages can be routed to different endpoints (e.g., local Ollama for drafts, Claude for reviews).

## Outline Generation (Stage 4)

**Step 4a — Chapter List (automatic chapter count determination):**
1. Generate chapter list based on story scope (AI determines appropriate chapter count)
2. **AI review** of entire chapter list for:
   - **Duplicate missions/events** across all chapters
   - **Narrative overlap** between chapters
   - **Clear progression** and structural issues
3. **Automatic revision** based on AI review
4. **Final AI review** of revised chapter list
5. User approval menu with:
   - `approve` — accept the chapter list (updates total_chapters in state)
   - `regenerate` — regenerate fresh (with optional guidance)
   - `regenerate with guidance` — regenerate with your notes
   - `use review` — regenerate using AI review as brief
   - `discuss / give feedback` — incorporate your feedback and regenerate
   - `edit manually` — paste your own version
6. After any user action that creates a new version, **final AI review** runs again
7. **Updates `total_chapters` in state** to match actual chapter count
8. Only after chapter list approved, proceed to individual outlines

**Note:** The chapter count is now determined by story scope during chapter list generation, not pre-specified in story bible.

**Step 4b — Individual Outlines (with continuity checking):**
1. Draft each outline with continuity checking against previous 3 outlines
2. AI review for:
   - **Duplicate missions/events** across previous outlines
   - **Narrative uniqueness** — each chapter has distinct purpose
   - **Plot progression** and timeline logic
   - **Continuity** with previous outlines
3. Revision based on review
4. **Final AI review** after every revision
5. User approval (with auto-approve support)

**Prevents duplicates at the source** — catches overlapping missions at chapter list level AND individual outline level before any prose is written.

## Chapter Generation Workflow (Stage 5)

**Per-chapter flow:**
1. Draft generation (`chapter_writer` model)
2. AI review (`chapter_reviewer` model) — 13-criterion rubric, scores 0-10
   - **NEW:** Checks for duplicate content across previous chapters
   - **NEW:** Cross-chapter consistency verification
   - Includes previous 2-3 chapters + macro summary for context
3. Revision based on review
4. **Final AI review** (runs after EVERY revision, including feedback/use review/regenerate)
5. User approval

**Final review triggers:**
- After initial revision (based on first AI review)
- After using "Use review" action (regenerate with AI review as brief)
- After user feedback action
- After regenerate action

**Auto-approve behavior:**
- "Approve all remaining" option appears in approval menu
- Requires **final review score ≥ 9.0** to enable
- Once enabled, automatically approves subsequent chapters if final review score ≥ 9.0
- If score drops below 9.0: auto-approve disables, shows warning, falls through to standard approval menu

**Key constants (`stage5_chapters.py`):**
```python
_LOW_SCORE_THRESHOLD = 7.0          # Warning threshold for initial review
_AUTO_APPROVE_SCORE_THRESHOLD = 9.0  # Threshold for auto-approve final review
_MAX_MACRO_WORDS = 2000             # Macro summary compression trigger
```

## Batch Review (Stage 6)

**Automatic stage after chapter completion:**
- Groups chapters into batches of 5 with 2-chapter overlap
- Example: Chapters [0-4], [2-6], [4-8], etc.

**Two-tier review:**
1. **Continuity check** (fast) — cross-chapter plot holes, character consistency, timeline issues
2. **Full quality review** (if issues found) — runs stage5-style 12-criterion rubric on each chapter

**Auto-approve for batches:**
- Requires continuity score ≥ 9.0 and no MAJOR issues
- Automatically approves clean batches
- Stops and shows menu if issues found

**Batch approval actions:**
- `approve` — mark batch as reviewed
- `approve all` — auto-approve remaining clean batches
- `skip` — skip this batch
- `regenerate` — shows which chapters need work and how to regenerate them
- `regenerate with guidance` — saves your guidance to a file, shows regeneration instructions
- `feedback` — saves your feedback to a file for use during regeneration
- `edit` — explains how to manually edit chapter files or use other options

**Regenerating chapters after batch review:**
```bash
# Regenerate specific problematic chapters
prosewrite run --name my_novel --stage chapters --chapter 4
prosewrite run --name my_novel --stage chapters --chapter 7

# Then run batch review again to verify fixes
prosewrite run --name my_novel --stage batch_review
```

## Configuration

**Per-stage model routing:**
```toml
[stages.chapter_writer]
model = "deepseek/deepseek-reasoner"
temperature = 1.5  # High for creativity

[stages.chapter_reviewer]
model = "deepseek/deepseek-chat"
temperature = 1.0  # Lower for critique
```

**Style injection:** `[style]` block (pov, tense, genre, min_words, notes) is injected into every chapter prompt.

**Persona injection:** `[persona]` block is prepended to all system prompts via `system_persona.txt` to fight generic prose.

## Prompt Files

All prompts in `prosewrite/prompts/` as editable `.txt` files. Placeholders use `[[double_brackets]]`.

**Key prompts:**
- `stage5_writer.txt` — Chapter prose generation
- `stage5_reviewer.txt` — 12-criterion review rubric
- `stage5_scene_brief.txt` — Internal scene brief (not shown to user)
- `stage5_revision.txt` — Revision instructions
- `system_persona.txt` — Master persona template

## State Management

**State file:** `projects/<name>/project_state.json`

**Progress tracking:**
- `approved_outlines: list[int]` — Chapter outlines approved
- `approved_chapters: list[int]` — Chapters approved
- `last_approved_chapter: int` — Resume point for stage 5

**Resume behavior:** CLI checks `current_stage` field and continues from that stage. Stage 5 skips already-approved chapters.

## Approval Actions

Natural language input mapped to actions (`approval.py`):
- `approve` / `yes` / `good` → Save and advance
- `regenerate` → Rerun from scratch
- `[feedback text]` → Incorporate and re-present
- `edit` → Wait for paste-back
- `skip` → Move to next (chapter stages only)
- `use review` → Regenerate using AI review as brief
- `approve all` → Enable auto-approve mode

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests use `httpx.BaseTransport` mocks to simulate LLM responses. No external API calls.

## Macro Summary

Auto-updated after each approved chapter (`summaries/macro.md`). Capped at 2000 words; older chapters compressed to bullets, recent 2-3 kept as narrative. Prevents continuity drift.

## Character Context in Stage 5

Only character profiles mentioned in the chapter outline are included in the writer context (line 40: `if char_name in outline_text.lower()`). Keeps token count controlled.

## CLI Entry Points

```bash
prosewrite new --name <name> --seed <seed.md>
prosewrite resume --name <name>
prosewrite run --name <name> --stage <stage_name>  # stage names: seed, bible, world, characters, outlines, chapters, batch_review, export
prosewrite run --name <name> --stage chapters --chapter <N>  # regenerate specific chapter
prosewrite export --name <name>
prosewrite list
```

**Stage names for `--stage` flag:**
- `seed` (stage0)
- `bible` (stage1)
- `world` (stage2)
- `characters` (stage3)
- `outlines` (stage4)
- `chapters` (stage5)
- `batch_review` (stage6)
- `export` (stage7)

**Chapter regeneration:**
```bash
# Regenerate a single chapter
prosewrite run --name my_novel --stage chapters --chapter 4

# This removes chapter 4 from approved_chapters and regenerates it
# Other approved chapters are skipped
```
