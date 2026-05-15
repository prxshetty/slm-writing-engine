"""
Configuration for LMStudio and the story framework.
"""

from pathlib import Path
from schema_loader import SchemaLoader

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os

LMSTUDIO = {
    "base_url": os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
    "model": os.getenv("LM_STUDIO_MODEL", ""),
    "temperature": 0.8,
    "max_tokens": 500,
    "stream": True,
}

REASONING_MODEL = os.getenv("REASONING_MODEL", "").lower() in ("true", "1", "yes")
DISABLE_TOKEN_LIMITS = os.getenv("DISABLE_TOKEN_LIMITS", "").lower() in ("true", "1", "yes")

THINKING_PREAMBLE = (
    "Before every response, you MUST think through the problem internally "
    "using this exact format:\n"
    "<|channel|>thought\n"
    "[your reasoning here]\n"
    "<channel|>\n\n"
    "Then provide your final answer after the closing tag.\n"
    "This format is REQUIRED for every response, including JSON outputs.\n\n"
)

def _resolve_max_tokens(key: str, default: int) -> int | None:
    """Resolve per-agent max_tokens — None when limits are disabled."""
    if DISABLE_TOKEN_LIMITS:
        return None
    return int(os.getenv(f"TOKENS_{key.upper()}", default))


AGENT_CONFIG = {
    "blueprint": {"max_tokens": _resolve_max_tokens("blueprint", 2000), "temperature": float(os.getenv("TEMPERATURE_BLUEPRINT", 0.9))},
    "scene": {"max_tokens": _resolve_max_tokens("scene", 600), "temperature": float(os.getenv("TEMPERATURE_SCENE", 0.8))},
    "dialogue": {"max_tokens": _resolve_max_tokens("dialogue", 800), "temperature": float(os.getenv("TEMPERATURE_DIALOGUE", 0.85))},
    "narration": {"max_tokens": _resolve_max_tokens("narration", 800), "temperature": float(os.getenv("TEMPERATURE_NARRATION", 0.8))},
    "decomposer": {"max_tokens": _resolve_max_tokens("decomposer", 600), "temperature": float(os.getenv("TEMPERATURE_DECOMPOSER", 0.8))},
    "transition": {"max_tokens": _resolve_max_tokens("transition", 400), "temperature": float(os.getenv("TEMPERATURE_TRANSITION", 0.7))},
    "writer": {"max_tokens": _resolve_max_tokens("writer", 500), "temperature": float(os.getenv("TEMPERATURE_WRITER", 0.85))},
}

TOKEN_LIMITS = {key: cfg["max_tokens"] for key, cfg in AGENT_CONFIG.items()}

SCHEMA = SchemaLoader()


def _load_prompt(filename: str) -> str:
    prompts_dir = Path(__file__).parent / "prompts"
    file_path = prompts_dir / filename
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"Error reading prompt file {file_path}: {e}")
        return ""


def _build_blueprint_prompt() -> str:
    """Build the blueprint agent prompt from schema + template."""
    base_prompt = _load_prompt("blueprint_base.txt")
    schema_section = SCHEMA.generate_blueprint_schema_section()
    return base_prompt.replace("{SCHEMA_SECTION}", schema_section)


def _build_agent_prompts() -> dict:
    """Build agent prompts."""
    return {
        "blueprint": _build_blueprint_prompt(),
        "scene": _load_prompt("scene.txt"),
        "dialogue": _load_prompt("dialogue.txt"),
        "narration": _load_prompt("narration.txt"),
        "decomposer": _load_prompt("decomposer.txt"),
        "transition": _load_prompt("transition.txt"),
        "writer": _load_prompt("writer.txt"),
    }


def _apply_thinking_preamble(prompts: dict) -> dict:
    """Prepend the thinking preamble to every system prompt if reasoning model is enabled."""
    if not REASONING_MODEL:
        return prompts
    return {key: THINKING_PREAMBLE + prompt for key, prompt in prompts.items()}


agent_prompts = _build_agent_prompts()
agent_prompts = _apply_thinking_preamble(agent_prompts)
SYSTEM_PROMPTS = {key: prompt for key, prompt in agent_prompts.items()}
