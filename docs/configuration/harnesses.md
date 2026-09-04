# Agent Harnesses

If you already pay for an AI coding helper — **OpenCode**, **Claude Code**, **Codex**, or **Antigravity** — Margin can use it instead of an API endpoint. The helper runs on your computer, edits your files, and Margin shows you what changed.

## Before you start

Install the helper and sign in using its own instructions, in your own terminal. Margin only starts the helper's program — it never sees or stores your sign-in. If a run fails with a sign-in error, the helper's own message is shown; sign in again in your terminal and retry.

## Finding your helpers

**Settings > Harnesses** lists each supported helper:

- `✓ Ready · <version>` — good to go.
- `✕ Not installed` — install and sign in to its program, then reopen Settings and it should appear.

If Margin can't find a helper you installed, enter its location by hand in the "Custom program" box for that helper.

## Configuration

- **Default Harness**: `None — use endpoint` (the normal setting — nothing changes) or one of your helpers. You can also pick a helper per message from the dropdown in the Assist panel; the inline bubble always follows the default.
- **Default model**: which model the helper should use. OpenCode and Antigravity show the list straight from the helper itself. Codex and Claude Code don't offer a list command, so Margin ships a built-in list for them[^1] — or just type any model name by hand.
- **Custom program**: the helper's location on your computer, for installs Margin can't find on its own.

[^1]: The built-in lists for Codex and Claude Code are updated with Margin releases. If a brand-new model is missing, type its name by hand.

## While it works

- You can keep writing — the editor never locks.
- The helper's progress appears in the panel as it works.
- When it finishes, its changes are highlighted in your document. Conflicts (you edited the same paragraph) keep your version and are flagged.
- **Accept** keeps the merged result; **Reject** removes the helper's changes but preserves yours.

## If something goes wrong

| What you see | What to do |
|---|---|
| `<Name> not found` | Install the helper's program, or enter its location in Settings > Harnesses. |
| Sign-in / login errors | Sign in to the helper in your terminal and retry. |
| The helper changed other files | Only the open file is merged and highlighted; other files update when you revisit them. |
