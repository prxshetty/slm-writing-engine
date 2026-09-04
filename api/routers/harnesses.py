"""Discovery endpoint for agent-harness CLIs (OpenCode, Claude Code, ...).

Detection answers only: is the executable available, and what version is it?
Authentication is deliberately out of scope — users install and authenticate
each CLI themselves. Auth failures surface as the CLI's own stderr during a run.
"""

import os
import subprocess

from fastapi import APIRouter

from api.services.file_storage import storage
from api.services import harness_env
from api.services.harness_registry import HARNESS_DESCRIPTORS

router = APIRouter(prefix="/api/harnesses", tags=["harnesses"])


def _detect_version(exe: str, version_args: list) -> str | None:
    try:
        r = subprocess.run(
            [exe, *version_args],
            capture_output=True, text=True, timeout=5,
            env=harness_env.normalized_env(),
        )
        if r.returncode == 0:
            out = (r.stdout or r.stderr).strip()
            if out:
                return out.splitlines()[0][:80]
    except Exception:
        pass
    return None


def _resolve_harness_exe(harness_id: str, desc: dict, overrides: dict) -> str | None:
    custom = (overrides.get(harness_id) or {}).get("executable")
    if custom and os.path.isfile(custom) and os.access(custom, os.X_OK):
        return custom
    return harness_env.which_harness(desc["command"])


@router.get("")
def list_harnesses():
    s = storage.get_settings()
    overrides = s.get("harnesses") or {}
    result = []
    for hid, desc in HARNESS_DESCRIPTORS.items():
        exe = _resolve_harness_exe(hid, desc, overrides)
        result.append({
            "id": hid,
            "name": desc["name"],
            "installed": bool(exe),
            "version": _detect_version(exe, desc.get("version_args") or ["--version"]) if exe else None,
        })
    return {"harnesses": result}


def _parse_models_output(text: str) -> list:
    """Parse `provider/model` lines (opencode) or `id<TAB>Name` lines (agy).
    Skips progress chatter like agy's 'Fetching available models...'."""
    models = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        first = line.split()[0]
        if "/" not in first and "-" not in first:
            continue  # progress chatter, headers, etc.
        parts = line.split(None, 1)
        models.append({
            "id": parts[0],
            "name": parts[1].strip() if len(parts) > 1 else parts[0],
        })
    return models[:100]


@router.get("/{harness_id}/models")
def harness_models(harness_id: str):
    from fastapi import HTTPException
    desc = HARNESS_DESCRIPTORS.get(harness_id)
    if not desc:
        raise HTTPException(status_code=404, detail="Unknown harness")
    if desc.get("static_models"):
        return {
            "models": [{"id": m, "name": m} for m in desc["static_models"]],
            "manual": False,
        }
    s = storage.get_settings()
    exe = _resolve_harness_exe(harness_id, desc, s.get("harnesses") or {})
    if not exe or not desc.get("models_args"):
        return {"models": [], "manual": True}
    try:
        r = subprocess.run(
            [exe, *desc["models_args"]],
            capture_output=True, text=True, timeout=15,
            env=harness_env.normalized_env(),
        )
        if r.returncode != 0:
            return {"models": [], "manual": True}
        models = _parse_models_output(r.stdout or "")
        return {"models": models, "manual": len(models) == 0}
    except Exception:
        return {"models": [], "manual": True}
