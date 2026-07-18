"""Claude-backed steps: EXTRACT (transcript/note -> ledger) and JUDGE.

Uses the Anthropic Messages API with structured outputs (output_config.format)
so every response is schema-valid JSON we can trust. Model: claude-opus-4-8 with
adaptive thinking; the Judge runs at high effort because correctness is the
whole product.
"""

from __future__ import annotations

import json

import anthropic

from . import DEFAULT_MODEL
from . import prompts
from .env import load_env


def _parse_json_response(response) -> dict:
    """output_config.format guarantees the first text block is valid JSON."""
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


class GuardianAgent:
    def __init__(self, client: anthropic.Anthropic | None = None,
                 model: str = DEFAULT_MODEL):
        if client is None:
            load_env()  # pick up ANTHROPIC_API_KEY from a .env if present
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    # -- Extraction -------------------------------------------------------
    def extract_ledger(self, source_label: str, text: str) -> list[dict]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": prompts.EXTRACT_SCHEMA},
            },
            system=prompts.EXTRACT_SYSTEM,
            messages=[{"role": "user",
                       "content": prompts.extract_user(source_label, text)}],
        )
        return _parse_json_response(response).get("claims", [])

    # -- Judgment ---------------------------------------------------------
    def judge(self, chart: dict, transcript: str, note: str, avs: str,
              candidate_group: str, description: str, evidence: dict) -> list[dict]:
        # Stable per-record context goes in a cached system prefix so the three
        # judge calls for one record reuse it instead of re-billing it.
        context = prompts.judge_context(chart, transcript, note, avs)
        system = [
            {"type": "text", "text": prompts.JUDGE_SYSTEM},
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ]
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": prompts.JUDGE_SCHEMA},
            },
            system=system,
            messages=[{
                "role": "user",
                "content": prompts.judge_user(
                    candidate_group, description, json.dumps(evidence, indent=2)),
            }],
        )
        return _parse_json_response(response).get("findings", [])
