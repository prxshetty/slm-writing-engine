# Agent Harnesses

Margin can delegate work to external agent CLIs — **OpenCode**, **Claude Code**, **Codex**, and **Agy** — so you can use your existing subscriptions instead of an API endpoint.

## Prerequisites

Install and authenticate each CLI using its own instructions, in your own terminal. Margin only shells out to the executable; it never sees or stores your harness credentials. If a run fails with an auth error, the CLI's own message is shown — re-authenticate in your terminal and retry.

## Detection

**Settings > Harnesses** lists each supported harness with its install status (`GET /api/harnesses`):

- `✓ Installed · <version>` — ready to use.
- `✕ Not installed` — install and authenticate its CLI, then reopen Settings to re-detect.

Detection checks the server's `PATH` (extended with common Homebrew locations, since GUI-launched servers can miss them). If your CLI lives elsewhere, set a custom path (below) — detection honors it.

## Configuration

- **Default Harness**: `None — use endpoint` (default, existing behavior unchanged) or any harness. The Assist panel dropdown overrides this per request; the inline bubble always follows the default.
- **Custom executable**: per-harness absolute path (e.g. `/opt/homebrew/bin/opencode`) for installs outside `PATH`.

These persist as `default_harness` and `harnesses: { "<id>": { "executable": "..." } }` in settings.

## Behavior

- The editor is snapshotted and flushed to disk when a run starts; the agent operates on fresh file state.
- Agent stdout streams as progress text. The editor remains editable throughout.
- On completion, agent changes are paragraph-level three-way merged against your snapshot and current edits. Same-paragraph conflicts keep your version and are reported.
- Accept persists the merged document; Reject restores your content (removing agent changes) on disk as well.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `<Name> not found` error | CLI not on server `PATH` — install it or set a custom executable path, then reopen Settings. |
| Auth / login errors in output | Authenticate the CLI in your terminal (`<cli> login` or equivalent) and retry. |
| Agent edited other files | Only the open file is merged/highlighted; other workspace files update on sidebar refresh. |
| Highlight spans unchanged text | Highlighting is paragraph-oriented (min–max range over AI-changed paragraphs) — expected for v1. |
