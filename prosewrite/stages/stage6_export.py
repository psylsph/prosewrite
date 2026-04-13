from __future__ import annotations

from pathlib import Path

from rich.table import Table

from ..config import ProjectConfig
from ..display import console, show_success, show_warning, word_count
from ..state import ProjectState, save_state


def run(cfg: ProjectConfig, project_dir: Path, state: ProjectState) -> None:
    """Stage 6 — Export all approved chapters to manuscript.md (and optionally .docx)."""
    chapters_dir = project_dir / "chapters"
    chapter_files = sorted(chapters_dir.glob("chapter_*.md")) if chapters_dir.exists() else []

    if not chapter_files:
        show_warning("No approved chapters found in chapters/ directory.")
        return

    # Build manuscript
    manuscript_parts: list[str] = []
    table = Table(title="Manuscript", show_lines=True)
    table.add_column("#", style="bold", justify="right")
    table.add_column("File")
    table.add_column("Words", justify="right")

    total_words = 0

    for chapter_file in chapter_files:
        text = chapter_file.read_text(encoding="utf-8")
        wc = word_count(text)
        total_words += wc

        # Extract chapter number for display
        import re
        m = re.search(r"chapter_(\d+)", chapter_file.name)
        num = m.group(1) if m else "?"

        table.add_row(num, chapter_file.name, f"{wc:,}")
        manuscript_parts.append(f"# Chapter {num}\n\n{text.strip()}\n")

    manuscript_text = "\n\n---\n\n".join(manuscript_parts)
    manuscript_path = project_dir / "manuscript.md"
    manuscript_path.write_text(manuscript_text, encoding="utf-8")
    show_success(f"manuscript.md written ({total_words:,} words total).")

    table.add_section()
    table.add_row("", "[bold]TOTAL[/bold]", f"[bold]{total_words:,}[/bold]")
    console.print(table)

    # Optional .docx export
    try:
        from docx import Document
        from docx.shared import Pt
        import re

        doc = Document()
        doc.add_heading(state.project_name, 0)

        for part in manuscript_parts:
            lines = part.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("# "):
                    doc.add_heading(line[2:], level=1)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=2)
                else:
                    doc.add_paragraph(line)

        docx_path = project_dir / "manuscript.docx"
        doc.save(str(docx_path))
        show_success(f"manuscript.docx written.")

    except ImportError:
        console.print("[dim]python-docx not installed — skipping .docx export.[/dim]")

    state.current_stage = "stage6_export"
    save_state(state, project_dir)
