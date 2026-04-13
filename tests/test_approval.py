import pytest

from prosewrite.approval import ApprovalAction, _classify


class TestClassify:
    """Test intent classification without any I/O."""

    def _action(self, text: str) -> ApprovalAction:
        action, _ = _classify(text)
        return action

    # APPROVE variants
    def test_yes(self):
        assert self._action("yes") == ApprovalAction.APPROVE

    def test_good(self):
        assert self._action("good") == ApprovalAction.APPROVE

    def test_looks_great(self):
        assert self._action("looks great") == ApprovalAction.APPROVE

    def test_approve(self):
        assert self._action("approve") == ApprovalAction.APPROVE

    def test_next(self):
        assert self._action("next") == ApprovalAction.APPROVE

    def test_lgtm(self):
        assert self._action("LGTM") == ApprovalAction.APPROVE

    def test_ship_it(self):
        assert self._action("ship it") == ApprovalAction.APPROVE

    # REGENERATE variants
    def test_redo(self):
        assert self._action("redo") == ApprovalAction.REGENERATE

    def test_again(self):
        assert self._action("again") == ApprovalAction.REGENERATE

    def test_not_right(self):
        assert self._action("not right") == ApprovalAction.REGENERATE

    def test_try_again(self):
        assert self._action("try again") == ApprovalAction.REGENERATE

    # USE_REVIEW variants
    def test_use_review(self):
        assert self._action("use review") == ApprovalAction.USE_REVIEW

    def test_apply_review(self):
        assert self._action("apply review") == ApprovalAction.USE_REVIEW

    def test_use_the_review(self):
        assert self._action("use the review") == ApprovalAction.USE_REVIEW

    # EDIT variants
    def test_ill_edit(self):
        assert self._action("i'll edit it") == ApprovalAction.EDIT

    def test_editing_myself(self):
        assert self._action("editing myself") == ApprovalAction.EDIT

    # SKIP
    def test_skip(self):
        assert self._action("skip") == ApprovalAction.SKIP

    # FEEDBACK fallback
    def test_specific_note_is_feedback(self):
        assert self._action("The pacing in paragraph 3 is too slow") == ApprovalAction.FEEDBACK

    def test_empty_ish_is_feedback(self):
        assert self._action("hmm not sure about the ending") == ApprovalAction.FEEDBACK

    def test_complex_instruction_is_feedback(self):
        text = "Make the dialogue between Sarah and Marcus sharper — she wouldn't concede that quickly"
        assert self._action(text) == ApprovalAction.FEEDBACK

    # REGENERATE + instruction → still REGENERATE but carries the brief
    def test_redo_with_instruction_is_regenerate(self):
        assert self._action("redo fix the recommendations") == ApprovalAction.REGENERATE

    def test_redo_with_instruction_carries_brief(self):
        action, returned = _classify("redo fix the recommendations")
        assert action == ApprovalAction.REGENERATE
        assert returned == "fix the recommendations"

    def test_regenerate_with_instruction_carries_brief(self):
        action, returned = _classify("regenerate but make it darker in tone")
        assert action == ApprovalAction.REGENERATE
        assert returned == "but make it darker in tone"

    def test_redo_alone_is_still_regenerate(self):
        assert self._action("redo") == ApprovalAction.REGENERATE

    def test_redo_with_filler_only_is_regenerate(self):
        # "redo please" — "please" is filler, no substantive content
        assert self._action("redo please") == ApprovalAction.REGENERATE

    # Case insensitivity
    def test_uppercase_yes(self):
        assert self._action("YES") == ApprovalAction.APPROVE

    def test_mixed_case_regenerate(self):
        assert self._action("REDO") == ApprovalAction.REGENERATE

    # Returned text
    def test_returns_raw_text_for_feedback(self):
        feedback = "The opening line needs to be stronger"
        action, returned = _classify(feedback)
        assert action == ApprovalAction.FEEDBACK
        assert returned == feedback

    def test_returns_raw_text_for_approve(self):
        _, returned = _classify("yes")
        assert returned == "yes"
