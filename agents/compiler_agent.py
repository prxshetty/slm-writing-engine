"""
Compiler Agent — smooths transitions between adjacent beat paragraphs.
Sequential build: receives tail of previous beat + full draft of next beat,
returns next beat with smoothed boundary.
"""

import llm
import config


COMPILER_SYSTEM_PROMPT = (
    "You are a Compiler Agent. You smooth transitions between adjacent "
    "paragraphs without rewriting content. Adjust only the boundary."
)


class CompilerAgent:
    """Smooths transitions between adjacent beat paragraphs."""

    def __init__(self):
        self.client = llm.LLMClient()
        self.template = config.SYSTEM_PROMPTS["compiler"]
        self.token_limit = config.TOKEN_LIMITS["compiler"]
        self.temperature = config.AGENT_CONFIG["compiler"]["temperature"]

    def smooth_next(self, prev_tail: str, next_draft: str) -> str:
        """Smooth the transition from prev_tail into next_draft.

        Args:
            prev_tail: Last ~2 sentences of the previous (already compiled) beat.
            next_draft: Full raw draft of the next beat from WriterAgent.

        Returns:
            next_draft with the first 1-2 sentences adjusted for smooth flow.
        """
        user_prompt = (
            self.template
            .replace("{prev_tail}", prev_tail)
            .replace("{next_draft}", next_draft)
        )
        return self.client.generate_to_completion(
            system_prompt=COMPILER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=self.temperature,
            max_tokens=self.token_limit,
        )
