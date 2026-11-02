"""Builds results/results.json and prints a markdown table. A's cost is API-equivalent:
same prompt tokens as C (identical prompt) at $5/M in, $25/M out (Opus list price assumption)."""
import json, random, statistics as st
from pathlib import Path
R = Path(__file__).resolve().parent.parent / "results"
def boot(xs, n=2000):
    rng = random.Random(0); m = [sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n)]; m.sort()
    return m[int(.025 * n)], m[int(.975 * n)]
rows = {}
C = {json.loads(l)["id"]: json.loads(l) for l in open(R / "test_C.jsonl")}
for s, label in [("A", "A: Claude Opus 5.5, full log"), ("C", "C: DeepSeek V4.1 Flash, full log"),
                 ("D", "D v1: Brand Brain (frozen)"), ("D2", "D v2: Brand Brain + verify pass")]:
    recs = [json.loads(l) for l in open(R / f"test_{s}.jsonl")]
    xs = [int(r["correct"]) for r in recs]
    if s == "A":
        cost = sum(C[r["id"]]["usage"]["in"] / 1e6 * 5 + r["usage"]["out"] / 1e6 * 25 for r in recs)
    else:
        cost = sum(r["usage"]["cost"] for r in recs)
    ingest = 0
    if s in ("D", "D2"):
        ing = json.load(open(R / ("test_ingest.json" if s == "D" else "test_v2_ingest.json")))
        ingest = ing["cost"] + ing.get("verify_cost", 0)
    bt = {}
    for r in recs: bt.setdefault(r["type"], []).append(int(r["correct"]))
    ans = [r for r in recs if r["answer"] is not None]; ab = [r for r in recs if r["answer"] is None]
    nd = lambda r: bool((r["pred"] or {}).get("not_decided")) or not str((r["pred"] or {}).get("answer") or "").strip()
    lo, hi = boot(xs)
    rows[s] = {"label": label, "n": len(xs), "acc": sum(xs) / len(xs), "ci95": [lo, hi],
               "by_type": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(bt.items())},
               "false_abstain": sum(nd(r) for r in ans), "hallucinated_on_abstain": sum(not nd(r) for r in ab),
               "query_cost_usd": cost, "ingest_cost_usd": ingest, "cost_per_correct": (cost + ingest) / max(1, sum(xs)),
               "p50_sec": st.median(r["usage"]["sec"] for r in recs),
               "prompt_tokens_per_q": sum((C[r["id"]] if s == "A" else r)["usage"]["in"] for r in recs) / len(recs)}
json.dump(rows, open(R / "results.json", "w"), indent=1)
print("| System | Accuracy (95% CI) | current | as_of | two_hop | abstain | $ total (incl. ingest) | $ per correct | prompt tok/q | p50 s |")
print("|---|---|---|---|---|---|---|---|---|---|")
for s, r in rows.items():
    b = r["by_type"]
    print(f"| {r['label']} | {r['acc']*100:.0f}% ({r['ci95'][0]*100:.0f}-{r['ci95'][1]*100:.0f}) | {b['current']} | {b['as_of']} | {b['two_hop']} | {b['abstain']} | "
          f"${r['query_cost_usd']+r['ingest_cost_usd']:.3f} | ${r['cost_per_correct']:.5f} | {r['prompt_tokens_per_q']:.0f} | {r['p50_sec']:.1f} |")
