# Prompts

Agent instructions are editable from **Settings > Context** (top section). Changes apply to the next request — no restart needed.

| Prompt | File | Used by |
|--------|------|---------|
| Writer | `simple-writer.md` | Panel edits and inline Rewrite — endpoint path only. Writes the replacement text. |
| Planner | `simple-planner.md` | Endpoint path only. Picks context files and refines the instruction for the Writer. |
| Chat | `simple-chat.md` | Conversational answers, no edits. Used by both endpoints and harnesses. |
| Harness Edit | `harness-edit.md` | Standing instructions for agent harnesses in Edit mode (OpenCode, Claude Code, Codex, Antigravity) — including the rule that changes land in files, not in the reply. |

The inline bubble's **Rewrite** action shares the Writer template (`simple-writer.md`) with panel edits — there is no separate inline prompt to maintain. It skips the Planner and sends your instruction straight to the Writer with paragraph context.
