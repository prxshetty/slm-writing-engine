"""
Style Loader — loads style markdown files from inputs/styles/.
"""

import yaml
import re
from pathlib import Path
from typing import Dict, Optional, Any, List

STYLES_DIR = Path(__file__).parent / "inputs" / "styles"
STYLES_MD_PATH = STYLES_DIR / "STYLES.md"


def load_style(name: str) -> Optional[Dict[str, Any]]:
    """Load a style by name from inputs/styles/."""
    path = STYLES_DIR / f"{name}.md"
    if path.exists():
        return _parse_style_file(path)
    return None


def load_all_styles() -> Dict[str, Dict[str, Any]]:
    """Load all styles using STYLES.md as the source of truth.

    Reads STYLES.md for the list of available styles, then loads each
    style's full data from its individual .md file. Validates that the
    STYLES.md entries match files on disk — raises ValueError on mismatch.
    """
    descs = read_styles_md()
    expected = set(descs.keys())

    actual = set()
    if STYLES_DIR.exists():
        for fpath in STYLES_DIR.glob("*.md"):
            if fpath.stem.lower() == "styles":
                continue
            actual.add(fpath.stem.lower())

    only_in_md = expected - actual
    only_on_disk = actual - expected

    if only_in_md:
        raise ValueError(
            f"Styles listed in STYLES.md but missing files: {sorted(only_in_md)}\n"
            f"Add matching .md files to inputs/styles/ or remove the entries from STYLES.md."
        )
    if only_on_disk:
        raise ValueError(
            f"Style files on disk but not listed in STYLES.md: {sorted(only_on_disk)}\n"
            f"Add entries to inputs/styles/STYLES.md, or delete the orphaned files."
        )

    styles = {}
    for name in expected:
        style = load_style(name)
        if style:
            styles[name] = style
    return styles


STYLES_MD_PATH = Path(__file__).parent / "inputs" / "styles" / "STYLES.md"


def generate_styles_md(path: Optional[Path] = None) -> str:
    """Generate STYLES.md from style files on disk.

    Skips if file already exists (user-owned). Delete STYLES.md or
    pass force=True via the caller to regenerate.
    """
    target = path or STYLES_MD_PATH
    if target.exists():
        return target.read_text(encoding="utf-8")

    # Scan disk directly (no validation — STYLES.md doesn't exist yet)
    style_files = {}
    if STYLES_DIR.exists():
        for fpath in STYLES_DIR.glob("*.md"):
            if fpath.stem.lower() == "styles":
                continue
            style_files[fpath.stem.lower()] = _parse_style_file(fpath)

    lines = [
        "# Available Styles",
        "",
        "Use these style tags when annotating scene_events.",
        "",
    ]
    for name in sorted(style_files):
        desc = style_files[name].get("description", "")
        lines.append(f"- **{name}** — {desc}")
    lines.append("")

    content = "\n".join(lines)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return content


def read_styles_md(path: Optional[Path] = None) -> Dict[str, str]:
    """Read STYLES.md and return {name: description}.

    Returns empty dict if file doesn't exist.
    """
    target = path or STYLES_MD_PATH
    if not target.exists():
        return {}

    content = target.read_text(encoding="utf-8")
    styles = {}
    for line in content.splitlines():
        m = re.match(r"^-\s+\*\*([^*]+)\*\*\s*—\s*(.+)", line)
        if m:
            styles[m.group(1).strip()] = m.group(2).strip()
    return styles


def _parse_style_file(path: Path) -> Dict[str, Any]:
    """Parse a style markdown file with YAML frontmatter + ## sections."""
    content = path.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    frontmatter = yaml.safe_load(fm_match.group(1)) if fm_match else {}
    body = content[fm_match.end():] if fm_match else content

    writer_match = re.search(
        r"## Writer Guidelines\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL
    )
    dialogue_match = re.search(
        r"## Dialogue Guidelines\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL
    )

    return {
        "description": frontmatter.get("description", ""),
        "required_agents": frontmatter.get("required_agents", []),
        "writer_guidelines": writer_match.group(1).strip() if writer_match else "",
        "dialogue_guidelines": dialogue_match.group(1).strip() if dialogue_match else "",
    }
