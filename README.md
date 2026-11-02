# Brand Brain

A memory layer that keeps the **current state of a brand** (promo code, free-shipping threshold, hero product, product prices and suppliers, target customer, ad cap, return window, agency) from a noisy Slack-style ops log. Every fact keeps its date, its source message and the fact it replaced. Built on Fireworks serverless open models for the Fireworks Deploy hackathon, 2 Nov 2026.

## Result in one line
On 120 held-out questions, Claude Opus 5.5 reading the whole log scores 100%. Brand Brain v2 on open models scores 79% at **about 1/160 of Opus's cost per correct answer**, with 43x fewer prompt tokens per question and 10x lower latency. **Brand Brain does not beat the frontier model on accuracy.** We report that as the headline.

| System | Accuracy (95% CI) | current | as_of | two_hop | abstain | $ total (incl. ingest) | $ per correct | prompt tok/q | p50 s |
|---|---|---|---|---|---|---|---|---|---|
| A: Claude Opus 5.5, full log | 100% (100-100) | 48/48 | 24/24 | 24/24 | 24/24 | $8.890 | $0.07408 | 14492 | 7.5 |
| C: DeepSeek V4.1 Flash, full log | 83% (76-89) | 48/48 | 18/24 | 10/24 | 24/24 | $0.524 | $0.00524 | 14492 | 0.7 |
| D v1: Brand Brain (frozen) | 27% (19-35) | 14/48 | 13/24 | 4/24 | 1/24 | $0.030 | $0.00093 | 339 | 0.6 |
| D v2: Brand Brain + verify pass | 79% (72-87) | 40/48 | 18/24 | 14/24 | 23/24 | $0.044 | $0.00047 | 339 | 0.7 |

Source: `results/results.json`, made by `src/report.py`. The CI is a bootstrap over questions.

**Read this before trusting D v2.** The verify pass was added *after* we saw v1's failures on the test set. D v2 is therefore not a clean held-out number. D v1 is the frozen, held-out result.

## Who it is for and why it matters
DTC ecommerce brands now run several AI tools at once: ad copy, support replies, email flows, product pages. Each tool needs the same current facts. In our customer calls, brands described DIY ChatGPT/Claude setups that forget context, and one asked for a shared knowledge layer across their automations. A stale promo code in live ads, or a wrong free-shipping threshold in a support reply, costs money the same day.

Pasting the whole history into a frontier model works, as system A shows. It costs about $0.07 per question at 14.5k tokens, and the cost grows with the log. Agents query memory many times a day, so a store that answers from about 340 tokens per question changes the unit economics. The open-model full-context baseline (C) is cheap but misses 14 of 24 two-hop questions, such as "who makes our hero product?". Brand Brain answers more of those (14/24) because it resolves the hero product first and then looks up that product.

## How it works
1. **Ingest (write):** NVIDIA Nemotron Lightning 3.5 30B A3B on Fireworks reads the log in batches of 20. It extracts `{msg_id, entity, attribute, value}` with attributes from a fixed enum. The validity date is always the message timestamp, set in code and never by the model.
2. **Verify (v2):** DeepSeek V4.1 Flash checks each source message once. It asks whether the message announces a decision, or is a proposal, a question or a recollection of an old value. Non-decisions are dropped.
3. **Store:** SQLite holds `facts(entity, attribute, value, valid_from, valid_to, source_id, source_text)`. A new value for the same key closes the previous one, which supports `current` and `as_of(date)` queries plus the full history.
4. **Read:** DeepSeek V4.1 Flash routes the question to `{entity, attribute, as_of, via_hero}` in one JSON call. Code does the lookup, including the hero-product hop. An empty lookup returns "not decided". The answer carries its date, its source message and what it replaced.

All model calls run at temperature 0 with reasoning off, on Fireworks serverless. Nothing was fine-tuned.

## Benchmark: Brand Ops Bench (self-built, in-distribution)
- `bench/generate.py` is pure `random.Random(seed)` with no model calls. It builds 6 fictional brands over 90 days, about 500 messages and 14.5k tokens per brand.
  - Each fact changes 1-3 times.
  - About 60% of messages are noise: chatter, proposals for things never decided, competitor mentions, and people recalling old values.
- **Questions:** 120 in total, 20 per brand.
  - 48 ask for a current fact.
  - 24 ask for a value as of a past date.
  - 24 are two-hop questions about an attribute of the hero product.
  - 24 are abstain questions about things discussed but never decided.
  - Question wording differs from the log wording, for example "minimum order value for free delivery" in the question against "free shipping threshold" in the log.
- **Held-out set:** the test seed is the code-freeze commit hash `c3c60bc5`, stored in `bench/test/FREEZE_COMMIT`. The test set was generated after that commit.
- **Grading:** every system must return `{"answer", "not_decided"}`. One normaliser applies to all systems: lowercase, strip `$` and punctuation, numeric compare for day counts, and a gold-substring match. Abstain questions pass only when the system says not decided.
- **Fair baseline:** A and C get the same system prompt (`BASE_SYS` in `src/bb.py`), which warns that facts change, that proposals and recollections are not decisions, and that they should say not decided when nothing was decided. The full log fits in context, so nothing is truncated.

## Limitations (honest list)
- **The benchmark is synthetic and self-built,** and the writer's attribute enum matches the generator's vocabulary. That makes extraction easier than on real Slack data. Treat the numbers as in-distribution.
- **D v2 was fixed after looking at test failures,** as stated above.
- **System A was run through the Claude Code CLI** (`claude -p --system-prompt ... --tools ""`) because no Anthropic or OpenAI API key was available at the event. A's cost is API-equivalent: the same prompt tokens as C at an assumed $5/M input and $25/M output for Opus 5.5. The CLI's own reported cost is higher because it adds harness context.
- **Brand Brain still loses 8 current-fact questions,** where a recollection slipped past verification or a value was misparsed. It also loses 10 two-hop questions, where a missed hero-product change sends the second hop to the wrong product.
- **Small sample:** n per type is 24-48, and provider nondeterminism can shift results by a few points on rerun.
- **Not done in time:** a hand-written out-of-distribution slice, a dev-set ablation of the verify pass, and unit tests.

## Reproduce
```bash
uv venv .venv && uv pip install --python .venv/bin/python httpx
echo "FIREWORKS_API_KEY=fw_..." > .env
python bench/generate.py --seed c3c60bc5 --name test   # regenerates the committed test set
.venv/bin/python src/bb.py ingest --set test            # Nemotron writer -> results/test.db
.venv/bin/python src/verify.py --set test               # v2 verify pass -> results/test_v2.db
.venv/bin/python src/bb.py eval --set test --system C   # D, D2, C on Fireworks; A needs the claude CLI
.venv/bin/python src/report.py
```
Models are Fireworks `accounts/fireworks/models/deepseek-v4p1-flash` (reader, verify, and system C) and `accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b` (writer). The frontier model is `claude-opus-5-5`. Per-question outputs, traces and token usage are in `results/test_*.jsonl`.

## Disclosure
- **Prior work:** a written proposal only. No code existed before 3:35pm PT on 2 Nov 2026.
- **AI tools:** Claude Code wrote the code.
- **Team:** Nodar Danelia and Alan Piovesana.
