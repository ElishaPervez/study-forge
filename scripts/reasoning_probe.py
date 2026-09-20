"""Probe which reasoning controls this model actually honours.

The model's metadata says it supports only max/high/low and that high is the
default. That makes a low-versus-high comparison useless on its own: a parameter
that is ignored looks exactly like a parameter set to its default. So every
variant here is read against the extremes - "max" on one side and "enabled:
false" on the other - where an honoured parameter cannot masquerade as an
ignored one.

Prices come from the model's own metadata, so each probe reports what it cost:

    uv run python -m scripts.reasoning_probe --reps 1 --only baseline
    uv run python -m scripts.reasoning_probe --reps 3 --budget 0.15
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field

import httpx

from backend.settings import load_settings

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"
PROVIDER = "deepseek"
DEFAULT_MAX_TOKENS = 200000
TIMEOUT_SECONDS = 900.0

# metadata rates, per token
PROMPT_RATE = 0.15e-6
CACHE_READ_RATE = 0.003e-6
COMPLETION_RATE = 0.60e-6

# The prompt has to make hidden reasoning the ONLY place the work can happen.
# Asked to show its working, this model enumerates in visible output instead and
# reports zero reasoning tokens whatever the parameters say, which would make
# every variant below look identical.
PROMPT = (
    "Work this out by hand, checking each candidate individually rather than "
    "estimating, and re-check your arithmetic before you answer.\n\n"
    "Consider every integer n with 150 < n < 240. For each n, decide whether n "
    "can be written as the sum of two squares of positive integers, allowing "
    "the two squares to be equal.\n\n"
    "Your entire reply must be the two integers on the final line, separated by "
    "a single space: how many such n there are, and the sum of all of them. Do "
    "not show working, do not explain, and do not list the numbers you considered."
)


@dataclass(frozen=True)
class Variant:
    """One parameter shape to put in front of the model."""

    label: str
    body: dict = field(default_factory=dict)
    max_tokens: int = DEFAULT_MAX_TOKENS
    require_parameters: bool = False


VARIANTS = (
    Variant("baseline: no reasoning parameter", {}),
    Variant("reasoning_effort=low (what the app sends)", {"reasoning_effort": "low"}),
    Variant("reasoning_effort=high (the model's default)", {"reasoning_effort": "high"}),
    Variant("reasoning_effort=max (the ceiling)", {"reasoning_effort": "max"}),
    Variant('reasoning={"effort":"low"} (nested)', {"reasoning": {"effort": "low"}}),
    Variant('reasoning={"effort":"max"} (nested)', {"reasoning": {"effort": "max"}}),
    Variant('reasoning={"max_tokens":256}', {"reasoning": {"max_tokens": 256}}),
    Variant('reasoning={"enabled":false}', {"reasoning": {"enabled": False}}),
    Variant("max_tokens=2000 (caps the whole budget)", {}, max_tokens=2000),
    Variant("max_tokens=8000", {}, max_tokens=8000),
    Variant("max_tokens=16000", {}, max_tokens=16000),
    Variant("require_parameters=true (diagnostic)", {}, require_parameters=True),
)


def build_body(variant: Variant) -> dict:
    body: dict = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": variant.max_tokens,
        "stream": False,
        # Reported cost, so the probe can police its own budget instead of guessing.
        "usage": {"include": True},
        "provider": {
            "only": [PROVIDER],
            "allow_fallbacks": False,
            "require_parameters": variant.require_parameters,
        },
    }
    body.update(variant.body)
    return body


def call(body: dict, api_key: str) -> tuple[dict | None, str | None]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = httpx.post(API_URL, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        return None, f"transport: {error}"
    if response.status_code != 200:
        detail = response.text[:300].replace("\n", " ")
        return None, f"HTTP {response.status_code}: {detail}"
    return response.json(), None


def cost_of(usage: dict) -> float:
    if isinstance(usage.get("cost"), (int, float)):
        return float(usage["cost"])
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    completion = int(usage.get("completion_tokens") or 0)
    return (
        (prompt - cached) * PROMPT_RATE
        + cached * CACHE_READ_RATE
        + completion * COMPLETION_RATE
    )


def run_variant(variant: Variant, api_key: str) -> dict:
    payload, error = call(build_body(variant), api_key)
    if error is not None:
        return {"label": variant.label, "error": error, "cost": 0.0, "samples": []}
    usage = payload.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "label": variant.label,
        "error": None,
        "cost": cost_of(usage),
        "samples": [
            {
                # This provider reports reasoning under completion_tokens_details,
                # not as a top-level field; reading the wrong one made every
                # variant look like it reasoned zero tokens.
                "reasoning": int(
                    completion_details.get("reasoning_tokens")
                    or usage.get("reasoning_tokens")
                    or 0
                ),
                "completion": int(usage.get("completion_tokens") or 0),
                "prompt": int(usage.get("prompt_tokens") or 0),
                "cached": int(prompt_details.get("cached_tokens") or 0),
                "reasoning_chars": len(message.get("reasoning") or ""),
                "finish": choice.get("finish_reason"),
                "answer": (message.get("content") or ""),
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe reasoning parameter handling.")
    parser.add_argument("--reps", type=int, default=3, help="calls per variant")
    parser.add_argument("--budget", type=float, default=0.15, help="stop above this spend")
    parser.add_argument(
        "--only", default=None, help="comma-separated substrings to filter variant labels"
    )
    args = parser.parse_args(argv)

    api_key = load_settings().openrouter_api_key
    needles = [part for part in (args.only or "").split(",") if part]
    variants = [
        v for v in VARIANTS if not needles or any(needle in v.label for needle in needles)
    ]
    print(f"model={MODEL} provider={PROVIDER} reps={args.reps} budget=${args.budget:.2f}")
    print(f"variants: {len(variants)}  estimated worst case: ", end="", flush=True)
    print(f"${len(variants) * args.reps * 8000 * COMPLETION_RATE:.3f} if every call used 8k output\n")

    spent = 0.0
    results: list[dict] = []
    for variant in variants:
        entry = {"label": variant.label, "error": None, "samples": [], "cost": 0.0}
        for index in range(args.reps):
            if spent + 0.006 > args.budget:
                print(f"! budget guard hit at ${spent:.4f}; stopping", file=sys.stderr)
                report(results, spent)
                return 1
            outcome = run_variant(variant, api_key)
            entry["cost"] += outcome["cost"]
            spent += outcome["cost"]
            if outcome["error"]:
                entry["error"] = outcome["error"]
                break
            entry["samples"].extend(outcome["samples"])
            last = outcome["samples"][0]
            preview = " ".join(last["answer"].split())[:40]
            preview = f"{preview} [reasoning_text={last['reasoning_chars']}ch]"
            print(
                f"  {variant.label[:38]:<38} reasoning={last['reasoning']:>6} "
                f"out={last['completion']:>6} finish={last['finish']:<9} "
                f"${outcome['cost']:.5f} running=${spent:.4f} | {preview}",
                flush=True,
            )
        results.append(entry)

    report(results, spent)
    return 0


def report(results: list[dict], spent: float) -> None:
    print()
    print("summary (reasoning tokens per call)")
    print(f"{'variant':<46} {'n':>2} {'reasoning':>22} {'output':>8} {'finish':>9}")
    for entry in results:
        if entry["error"]:
            print(f"{entry['label'][:45]:<46} {'-':>2}  ERROR: {entry['error'][:60]}")
            continue
        samples = [s for s in entry["samples"] if s.get("reasoning") is not None]
        if not samples:
            continue
        reasoning = [s["reasoning"] for s in samples]
        spread = (
            f"{reasoning[0]}" if len(set(reasoning)) == 1 else f"{min(reasoning)}-{max(reasoning)}"
        )
        median = statistics.median(reasoning)
        print(
            f"{entry['label'][:45]:<46} {len(samples):>2} {spread:>14} (med {median:>6.0f}) "
            f"{samples[-1]['completion']:>8} {samples[-1]['finish']!s:>9}"
        )
    print(f"\ntotal spent: ${spent:.4f}")


if __name__ == "__main__":
    sys.exit(main())
