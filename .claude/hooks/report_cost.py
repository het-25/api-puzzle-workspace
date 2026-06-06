#!/usr/bin/env python3
"""
Claude Code Stop hook: report the cost of the turn that just finished to an API endpoint.

How it works
------------
The Stop hook fires once per turn (after Claude finishes responding). It receives a JSON
payload on stdin that includes `transcript_path` and `session_id`. The transcript is a
JSONL file; each `assistant` line carries `message.usage` (token counts) and `message.model`,
but NO precomputed cost. So we:

  1. Read the transcript.
  2. Sum the token usage of every assistant API request in the turn that just ended
     (everything since the last real user prompt).
  3. Compute USD cost from a per-model price table.
  4. POST {session_id, model, tokens, cost_usd, ...} to your endpoint with your API key.

Posts to the agent-mining-competition /ai-spend endpoint. Body shape:
  { "model": str, "inputTokens": int, "outputTokens": int, "estimatedCost": int }
`estimatedCost` is an integer, reported in MICRO-USD (USD x 1,000,000) so sub-cent
turns aren't lost to rounding.

Config (environment variables)
-------------------------------
  CLAUDE_COST_API_KEY    Required. Your agent API key. Sent as `Authorization: Bearer <key>`.
  CLAUDE_COST_ENDPOINT   Optional. Overrides the default endpoint below.
  CLAUDE_COST_TIMEOUT    Optional. Request timeout in seconds (default 5).

The endpoint is not a secret and is baked in as a default. Keep the API key OUT of
committed config — put it in `.claude/settings.local.json` (gitignored) or export it.

This script never fails the turn: any error is swallowed and it exits 0.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# Where to report. Not a secret; overridable via CLAUDE_COST_ENDPOINT.
DEFAULT_ENDPOINT = "https://agent-mining-competition-six.vercel.app/ai-spend"

# estimatedCost must be a whole integer. Report in micro-USD (USD * 1e6) to preserve
# sub-cent precision (a single turn is often a fraction of a cent).
COST_UNIT_PER_USD = 1_000_000

# USD per 1,000,000 tokens. Cache write = 1.25x input (5m TTL) / 2x input (1h TTL);
# cache read = 0.1x input. Keyed by model-id prefix so dated variants still match.
PRICING = {
    "claude-opus-4-8":   {"input": 5.00, "output": 25.00},
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00},
}


def price_for(model):
    """Return the price row for a model id, matching by prefix; None if unknown."""
    if not model:
        return None
    for prefix, row in PRICING.items():
        if model.startswith(prefix):
            return row
    return None


def is_real_user_turn(obj):
    """True if this transcript line is an actual user prompt (not a tool_result)."""
    if obj.get("type") != "user":
        return False
    content = obj.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        # A real prompt has text/image blocks; tool results are type "tool_result".
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def cost_of_usage(usage, price):
    """Compute USD cost for one assistant request's usage block."""
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    read = usage.get("cache_read_input_tokens", 0) or 0

    # Split cache writes into 5m / 1h when available for precise pricing.
    cc = usage.get("cache_creation", {}) or {}
    write_5m = cc.get("ephemeral_5m_input_tokens")
    write_1h = cc.get("ephemeral_1h_input_tokens")
    if write_5m is None and write_1h is None:
        # Fall back to the combined field, priced at the 5m rate.
        write_5m = usage.get("cache_creation_input_tokens", 0) or 0
        write_1h = 0

    in_rate = price["input"]
    cost = (
        inp * in_rate
        + out * price["output"]
        + read * (in_rate * 0.10)
        + (write_5m or 0) * (in_rate * 1.25)
        + (write_1h or 0) * (in_rate * 2.0)
    ) / 1_000_000.0
    return cost, {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_write_5m_input_tokens": write_5m or 0,
        "cache_write_1h_input_tokens": write_1h or 0,
    }


def collect_last_turn(transcript_path):
    """Sum usage + cost across every assistant request since the last real user prompt."""
    lines = []
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)

    total_cost = 0.0
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_5m_input_tokens": 0,
        "cache_write_1h_input_tokens": 0,
    }
    model = None
    requests = 0
    unpriced_models = set()

    # Walk backwards: gather assistant turns until we hit the user prompt that started them.
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_real_user_turn(obj):
            break
        if obj.get("type") == "assistant":
            msg = obj.get("message", {})
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            model = msg.get("model") or model
            price = price_for(msg.get("model"))
            if price is None:
                if msg.get("model"):
                    unpriced_models.add(msg["model"])
                continue
            cost, broken = cost_of_usage(usage, price)
            total_cost += cost
            requests += 1
            for k in totals:
                totals[k] += broken[k]

    return {
        "model": model,
        "requests": requests,
        "cost_usd": round(total_cost, 6),
        "tokens": totals,
        "unpriced_models": sorted(unpriced_models),
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    api_key = os.environ.get("CLAUDE_COST_API_KEY")
    if not api_key:
        # Not configured for this user — silently do nothing.
        return
    endpoint = os.environ.get("CLAUDE_COST_ENDPOINT", DEFAULT_ENDPOINT)

    transcript_path = payload.get("transcript_path")
    if not transcript_path or not os.path.exists(transcript_path):
        return

    try:
        turn = collect_last_turn(transcript_path)
    except Exception:
        return

    # `model` is required (min length 1). Nothing priced this turn -> nothing to report.
    if not turn["model"] or turn["requests"] == 0:
        return

    tk = turn["tokens"]
    # All input-side tokens (fresh + cached reads + cache writes); estimatedCost already
    # prices the cache discounts correctly, so this field is the raw input volume.
    input_tokens = (
        tk["input_tokens"]
        + tk["cache_read_input_tokens"]
        + tk["cache_write_5m_input_tokens"]
        + tk["cache_write_1h_input_tokens"]
    )

    body = {
        "model": turn["model"],
        "inputTokens": input_tokens,
        "outputTokens": tk["output_tokens"],
        "estimatedCost": round(turn["cost_usd"] * COST_UNIT_PER_USD),
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    timeout = float(os.environ.get("CLAUDE_COST_TIMEOUT", "5"))
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        # Never let a reporting failure disrupt the session.
        return


if __name__ == "__main__":
    main()
