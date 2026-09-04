"""Central registry of supported agent-harness CLIs.

Both `api/routers/assist.py` (execution) and `api/routers/harnesses.py`
(discovery) import from here — descriptors live in exactly one place.
"""

from typing import Dict, List, Optional, TypedDict


class HarnessDescriptor(TypedDict, total=False):
    name: str
    command: str
    version_args: List[str]
    models_args: List[str]
    static_models: List[str]
    subcommand: str
    workspace_flag: str
    prompt_flag: str
    model_flag: str
    cwd_flag: str
    agent_flag: str
    mode_agents: dict
    extra_args: List[str]
    structured: bool


HARNESS_DESCRIPTORS: Dict[str, HarnessDescriptor] = {
    "opencode": {
        "name": "OpenCode",
        "command": "opencode",
        "version_args": ["--version"],
        "models_args": ["models"],
        "subcommand": "run",
        # --dir pins the project root: `run` otherwise resolves it
        # itself (and created files outside the workspace).
        "workspace_flag": "--dir",
        "model_flag": "-m",
        # Margin chat → plan (read-only), Margin edit → build (edits files).
        "agent_flag": "--agent",
        "mode_agents": {"chat": "plan", "edit": "build"},
        # Structured stdout: `run --format json` emits raw JSON events
        # (text/reasoning/tool_use/step_finish). Opencode-only.
        "structured": True,
    },
    "claude-code": {
        "name": "Claude Code",
        "command": "claude",
        "version_args": ["--version"],
        "models_args": [],
        "static_models": [
            "claude-fable-5-1",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
        ],
        "prompt_flag": "-p",
        "model_flag": "--model",
    },
    "codex": {
        "name": "Codex",
        "command": "codex",
        "version_args": ["--version"],
        "models_args": [],
        # Codex offers no list-models command: built-in list, updated
        # with Margin releases. Custom IDs always typeable in Settings.
        "static_models": [
            "gpt-6-astra",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
        ],
        "subcommand": "exec",
        "workspace_flag": "-C",
        "model_flag": "-m",
    },
    "agy": {
        "name": "Antigravity",
        "command": "agy",
        "version_args": ["--version"],
        "models_args": ["models"],
        "workspace_flag": "--add-dir",
        "prompt_flag": "--print",
        "model_flag": "--model",
        # Headless print mode auto-denies permission prompts; accept-edits
        # pre-approves file edits (review still happens in Margin's diff UI).
        "extra_args": ["--mode", "accept-edits"],
    },
}


def get_descriptor(harness_id: str) -> Optional[HarnessDescriptor]:
    return HARNESS_DESCRIPTORS.get(harness_id)
