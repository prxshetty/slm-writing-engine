t3code provider icons

Extracted from:
pingdotgg/t3code
apps/web/src/components/Icons.tsx
apps/web/src/components/chat/providerIconUtils.ts

Provider mapping:
- codex -> OpenAI
- claudeAgent -> ClaudeAI
- opencode -> OpenCodeIcon
- cursor -> CursorIcon
- grok -> GrokIcon
- antigravity -> AntigravityIcon

Note: t3code's AntigravityIcon is an SVG wrapper around an embedded PNG data URI.
antigravity.svg here embeds agy.png the same way, so the file is self-contained
(no remote href). opencode.svg was normalized to a single currentColor mark
(rectangle with transparent center) to match the app's HarnessIcon; openai.svg
already uses currentColor. All three adapt to the surrounding text color.
