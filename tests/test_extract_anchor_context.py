"""Tests for extract_anchor_context — the core paragraph-extraction logic.

Covers:
- Full-paragraph selection
- Sub-paragraph (sentence-level) selection with before/after context split
- Cursor-based paragraph detection
- Fallback when no selection or cursor text
- Truncated selected_text[:50] fallback for long selections
"""
import sys
import types
from pathlib import Path

# Add project root so we can import from the api package
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Stub heavy third-party modules that assist.py imports at module level
# but that extract_anchor_context does not use.
for mod_name in [
    "sse_starlette",
    "sse_starlette.sse",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["sse_starlette.sse"].EventSourceResponse = lambda *a, **kw: None

# Stub the project-level modules that have heavy side-effects
for mod_name in ["llm", "config"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["config"].DISABLE_TOKEN_LIMITS = False
sys.modules["llm"].LLMClient = type("LLMClient", (), {})

# Stub api.services sub-modules (the real api package will load, but
# file_storage and context_injector pull in platformdirs etc.)
for mod_name in [
    "api.services.file_storage",
    "api.services.context_injector",
]:
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        sys.modules[mod_name] = m

sys.modules["api.services.file_storage"].storage = types.SimpleNamespace()

# Now import the function under test
from api.routers.assist import extract_anchor_context

# ──────────────────────────────────────────────────────────────────────────────
# Test data
# ──────────────────────────────────────────────────────────────────────────────
SAMPLE_CONTENT = """\
The old lighthouse stood at the edge of the cliff, its paint peeling in long strips that fluttered in the salt wind. It had not been operational for decades, but the townspeople refused to let it be demolished. Some said it was stubbornness; others called it reverence.

Every evening, Elara would climb the narrow stairs to the top and watch the sun sink into the ocean. The view from there was breathtaking — a ribbon of gold stretched across the horizon, slowly swallowed by indigo. She had been coming here since she was a child, when her grandmother first brought her up the iron steps.

Tonight was different. Tonight, the air felt heavier, charged with something unspoken. The usual peace of the place was replaced by a low hum that seemed to rise from the rocks themselves. Elara paused on the landing, her hand resting on the cold railing, and listened."""

PARAGRAPHS = [p for p in SAMPLE_CONTENT.split('\n\n') if p.strip()]


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────
def test_full_paragraph_selection():
    """Selecting the entire second paragraph returns it as target with neighbours."""
    selected = PARAGRAPHS[1]
    before, target, after, idx, replace = extract_anchor_context(
        SAMPLE_CONTENT, selected
    )
    assert replace is True
    assert idx == 1
    assert target.strip() == selected.strip()
    assert before.strip() == PARAGRAPHS[0].strip()
    assert after.strip() == PARAGRAPHS[2].strip()


def test_sub_paragraph_selection():
    """Selecting a sentence inside a paragraph splits that paragraph."""
    selected = "Tonight, the air felt heavier, charged with something unspoken."
    before, target, after, idx, replace = extract_anchor_context(
        SAMPLE_CONTENT, selected
    )
    assert replace is True
    assert target.strip() == selected.strip()
    # The surrounding text in the same paragraph must appear in before/after
    assert "The usual peace" in after or "The usual peace" in before
    assert before != "" or after != ""


def test_sub_paragraph_context_split():
    """Sub-paragraph selection correctly extracts before/after within same paragraph."""
    paragraph = (
        "She had been coming here since she was a child, "
        "when her grandmother first brought her up the iron steps."
    )
    content = f"Para before.\n\n{paragraph}\n\nPara after."
    selected = "when her grandmother first brought her up the iron steps"
    before, target, after, idx, replace = extract_anchor_context(content, selected)
    assert replace is True
    assert target.strip() == selected.strip()
    assert "She had been coming here since she was a child" in before
    # after contains whatever text follows the selected text in the paragraph
    assert after.strip() in ("", ".")  # trailing period or empty


def test_sub_paragraph_first_sentence():
    """Selecting the first sentence of a paragraph has empty before-text."""
    paragraph = (
        "The old lighthouse stood at the edge of the cliff, its paint peeling "
        "in long strips that fluttered in the salt wind. It had not been "
        "operational for decades, but the townspeople refused to let it be "
        "demolished. Some said it was stubbornness; others called it reverence."
    )
    content = f"Para before.\n\n{paragraph}\n\nPara after."
    selected = "The old lighthouse stood at the edge of the cliff, its paint peeling in long strips that fluttered in the salt wind."
    before, target, after, idx, replace = extract_anchor_context(content, selected)
    assert replace is True
    assert target.strip() == selected.strip()
    assert before.strip() == ""
    assert "It had not been operational" in after


def test_sub_paragraph_middle_sentence():
    """Selecting a middle sentence has both before and after text."""
    paragraph = (
        "The old lighthouse stood at the edge of the cliff. "
        "It had not been operational for decades. "
        "Some said it was stubbornness."
    )
    content = f"Para before.\n\n{paragraph}\n\nPara after."
    selected = "It had not been operational for decades."
    before, target, after, idx, replace = extract_anchor_context(content, selected)
    assert replace is True
    assert target.strip() == selected.strip()
    assert "The old lighthouse" in before
    assert "Some said it was stubbornness" in after


def test_cursor_based_paragraph_detection():
    """cursor_paragraph_text locates the correct paragraph."""
    cursor = "Every evening, Elara would climb the narrow stairs"
    before, target, after, idx, replace = extract_anchor_context(
        SAMPLE_CONTENT, None, cursor
    )
    assert replace is False
    assert idx == 1
    assert "Every evening" in target


def test_no_selection_no_cursor():
    """Defaults to last paragraph when no selection or cursor."""
    before, target, after, idx, replace = extract_anchor_context(
        SAMPLE_CONTENT, None, None
    )
    assert replace is False
    assert idx == len(PARAGRAPHS) - 1
    assert "Tonight was different" in target


def test_truncated_text_fallback():
    """Falls back to first 50 chars when full selected_text not found."""
    long_text = PARAGRAPHS[1] + " [THIS PART DOES NOT EXIST IN PARAGRAPH]"
    selected = long_text[:80]  # first 50 chars ARE in the paragraph
    before, target, after, idx, replace = extract_anchor_context(
        SAMPLE_CONTENT, selected
    )
    assert replace is True
    assert idx == 1


def test_single_paragraph_content():
    """Single paragraph — no before or after."""
    content = "This is the only paragraph."
    selected = "This is the only paragraph."
    before, target, after, idx, replace = extract_anchor_context(content, selected)
    assert replace is True
    assert idx == 0
    assert target.strip() == selected.strip()
    assert before == ""
    assert after == ""


def test_selected_text_not_found():
    """Unfound selected_text defaults to last paragraph."""
    content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    selected = "This text does not exist anywhere"
    before, target, after, idx, replace = extract_anchor_context(content, selected)
    assert replace is True
    assert idx == 2


def test_empty_paragraphs_filtered():
    """Blank lines between paragraphs are filtered out."""
    content = "First.\n\n\n\nSecond.\n\n\n\nThird."
    selected = "Second."
    before, target, after, idx, replace = extract_anchor_context(content, selected)
    assert replace is True
    assert target.strip() == "Second."
    assert before.strip() == "First."
    assert after.strip() == "Third."


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
