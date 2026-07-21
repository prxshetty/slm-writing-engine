from pathlib import Path


def extract_anchor_context(
    content: str,
    selected_text: str | None,
    cursor_paragraph_text: str | None = None,
) -> tuple[str, str, str, int, bool]:
    """
    Returns paragraph_before, target_paragraph, paragraph_after, target_idx, replace.
    replace=True  → caller should overwrite the target paragraph.
    replace=False → caller should insert new content after the target paragraph.
    """
    paragraphs = [p for p in content.split('\n\n') if p.strip()]
    target_idx = len(paragraphs) - 1
    replace = False

    if selected_text:
        replace = True
        found = False
        match_pos = -1

        for i, p in enumerate(paragraphs):
            pos = p.find(selected_text)
            if pos != -1:
                target_idx = i
                found = True
                match_pos = pos
                break

        if not found:
            for i, p in enumerate(paragraphs):
                pos = p.find(selected_text[:50])
                if pos != -1:
                    target_idx = i
                    found = True
                    match_pos = pos
                    break

        if found:
            target_p = paragraphs[target_idx]
            if match_pos != -1 and selected_text.strip() != target_p.strip():
                paragraph_before = target_p[:match_pos]
                paragraph_after = target_p[match_pos + len(selected_text):]
                return paragraph_before, selected_text, paragraph_after, target_idx, replace

    elif cursor_paragraph_text:
        cursor_text = cursor_paragraph_text.strip()
        for i, p in enumerate(paragraphs):
            if cursor_text[:60] in p:
                target_idx = i
                break

    target_paragraph = paragraphs[target_idx] if paragraphs else ""
    paragraph_before = paragraphs[target_idx - 1] if target_idx > 0 else ""
    paragraph_after = paragraphs[target_idx + 1] if target_idx < len(paragraphs) - 1 else ""
    return paragraph_before, target_paragraph, paragraph_after, target_idx, replace


def _load_simple_prompt(filename: str) -> str:
    path = Path(__file__).parent.parent.parent / "prompts" / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"Error loading prompt {filename} from {path}: {e}")
        return ""
