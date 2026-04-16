from __future__ import annotations

import re

from .client import LLMClient
from .config import resolve_stage
from .display import ReviewResult
from .pipeline import Pipeline


def _extract_score(review_text: str) -> float:
    """Extract numeric score from reviewer output. Returns 0.0 if not found."""
    match = re.search(
        r"OVERALL SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10", review_text, re.IGNORECASE
    )
    if match:
        return float(match.group(1))
    # Fallback: look for standalone N/10 pattern
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*/\s*10\b", review_text)
    if match:
        return float(match.group(1))
    return 0.0


def _extract_revision_brief(review_text: str) -> str:
    """Extract the REVISION BRIEF section from reviewer output."""
    match = re.search(
        r"REVISION BRIEF[^:]*:(.*?)$", review_text, re.IGNORECASE | re.DOTALL
    )
    if match:
        return match.group(1).strip()
    return ""


def _extract_summary(review_text: str) -> str:
    """Extract TOP 3 ISSUES as a short summary."""
    match = re.search(
        r"TOP 3 ISSUES[^:]*:(.*?)(?:WHAT WORKS|$)",
        review_text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return review_text[:300]


class AIReviewer:
    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self.cfg = pipeline.cfg

    def review_chapter(
        self,
        chapter_num: int,
        chapter_draft: str,
        chapter_outline: str,
        character_profiles: str,
        word_count: int,
        previous_chapters: str = "",
        macro_summary: str = "",
    ) -> ReviewResult:
        stage_cfg = resolve_stage(self.cfg, "chapter_outline_review")
        system = self.pipeline.build_system_prompt("chapter_outline_review")
        user_prompt = self.pipeline.build_user_prompt(
            "stage5_reviewer.txt",
            project_name=self.cfg.name,
            chapter_num=str(chapter_num),
            chapter_draft=chapter_draft,
            chapter_outline=chapter_outline,
            previous_chapters=previous_chapters,
            macro_summary=macro_summary,
            character_profiles=character_profiles,
            pov=self.cfg.style.pov,
            tense=self.cfg.style.tense,
            genre=self.cfg.style.genre,
            min_words=str(self.cfg.style.min_words),
            word_count=str(word_count),
        )
        with LLMClient(stage_cfg) as client:
            review_text = client.complete(
                system, [{"role": "user", "content": user_prompt}]
            )

        return ReviewResult(
            score=_extract_score(review_text),
            summary=_extract_summary(review_text),
            full_text=review_text,
            revision_brief=_extract_revision_brief(review_text),
        )

    def review_outline(
        self,
        chapter_num: int,
        outline_text: str,
        story_bible: str,
        character_index: str,
        previous_outlines: str = "",
    ) -> ReviewResult:
        """Review chapter outline with continuity checking against previous outlines."""
        stage_cfg = resolve_stage(self.cfg, "chapter_outline_review")
        system = self.pipeline.build_system_prompt("chapter_outline_review")

        user_prompt = self.pipeline.build_user_prompt(
            "stage4_outline_review.txt",
            project_name=self.cfg.name,
            chapter_num=str(chapter_num),
            outline_text=outline_text,
            previous_outlines=previous_outlines,
            story_bible=story_bible[:4000],
            character_index=character_index[:3000],
            chapter_list_entry=f"Chapter {chapter_num}",
        )

        with LLMClient(stage_cfg) as client:
            review_text = client.complete(
                system, [{"role": "user", "content": user_prompt}]
            )

        return ReviewResult(
            score=_extract_score(review_text),
            summary=_extract_summary(review_text),
            full_text=review_text,
            revision_brief=_extract_revision_brief(review_text),
        )

    def review_chapter_list(
        self,
        chapter_list: str,
        story_bible: str,
        world_content: str,
        character_index: str,
    ) -> ReviewResult:
        """Review the entire chapter list for duplicates and structural issues."""
        stage_cfg = resolve_stage(self.cfg, "chapter_list_review")
        system = self.pipeline.build_system_prompt("chapter_list_review")

        user_prompt = self.pipeline.build_user_prompt(
            "stage4_chapter_list_review.txt",
            project_name=self.cfg.name,
            chapter_list=chapter_list,
            story_bible=story_bible[:4000],
            world_content=world_content[:3000],
            character_index_content=character_index[:3000],
        )

        with LLMClient(stage_cfg) as client:
            review_text = client.complete(
                system, [{"role": "user", "content": user_prompt}]
            )

        return ReviewResult(
            score=_extract_score(review_text),
            summary=_extract_summary(review_text),
            full_text=review_text,
            revision_brief=_extract_revision_brief(review_text),
        )
