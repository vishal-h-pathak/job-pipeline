"""scripts/verify_prompt_cache.py — Session I task-4 verification.

Makes two identical cheap classifier-shaped calls and prints the usage
counters, demonstrating that the shared tailor system prefix
(`prompts.cached_system_blocks`) is written to the prompt cache on the
first call and served as cache reads on the second.

Run from the repo root with the standard env loaded:

    .venv/bin/python jobpipe/tailor/scripts/verify_prompt_cache.py

Expected shape of the output:

    call 1: cache_creation ≈ prefix tokens, cache_read = 0
    call 2: cache_creation = 0, cache_read ≈ prefix tokens

Costs nothing meaningful (two ~13k-input-token calls, 300 max output
tokens each, second one ~90% cached).
"""

from __future__ import annotations

import sys
from pathlib import Path

_TAILOR_DIR = Path(__file__).resolve().parent.parent
if str(_TAILOR_DIR) not in sys.path:
    sys.path.insert(0, str(_TAILOR_DIR))

from dotenv import load_dotenv

load_dotenv()

import anthropic  # noqa: E402

from jobpipe.config import ANTHROPIC_API_KEY, TAILOR_CLAUDE_MODEL  # noqa: E402
import prompts as tailor_prompts  # noqa: E402
from tailor.archetype import _archetypes_block_for_classifier  # noqa: E402


def main() -> None:
    blocks = tailor_prompts.cached_system_blocks()
    print(f"model: {TAILOR_CLAUDE_MODEL}")
    print(
        f"cached prefix: {len(blocks[0]['text'])} chars "
        f"(~{len(blocks[0]['text']) // 4} tokens, chars/4 estimate)"
    )

    user = tailor_prompts.load_task_prompt(
        "classify_archetype",
        archetypes_block=_archetypes_block_for_classifier(),
        job_title="Forward Deployed Engineer",
        company="ExampleAI",
        tier=2,
        job_desc="Build and deploy LLM agent systems for enterprise customers.",
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    for i in (1, 2):
        resp = client.messages.create(
            model=TAILOR_CLAUDE_MODEL,
            max_tokens=300,
            system=blocks,
            messages=[{"role": "user", "content": user}],
        )
        u = resp.usage
        print(
            f"call {i}: input={u.input_tokens} "
            f"cache_creation={getattr(u, 'cache_creation_input_tokens', None)} "
            f"cache_read={getattr(u, 'cache_read_input_tokens', None)} "
            f"output={u.output_tokens}"
        )


if __name__ == "__main__":
    main()
