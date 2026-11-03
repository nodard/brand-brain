"""Ask Brand Brain a question. Shows the answer with its date, source message and what it replaced.

  uv run src/ask.py --set test --brand "Ferro Coffee" "What is the discount code we should be advertising right now?"
  uv run src/ask.py --set test --brand "Ferro Coffee" --v1 "..."      # use the unverified v1 store
"""
import argparse, asyncio, json, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import bb


async def main(a):
    data = json.load(open(bb.ROOT / "bench" / a.set / "logs.json"))
    if a.brand not in data:
        sys.exit(f"unknown brand; choose one of: {', '.join(data)}")
    con = bb.open_db(a.set + ("" if a.v1 else "_v2"))
    pred, usage, trace = await bb.answer_D(con, a.brand, data[a.brand]["skus"], {"question": a.question}, asyncio.Semaphore(1))
    if a.json:
        print(json.dumps({"pred": pred, "trace": trace, "usage": usage}, indent=1, default=str)); return
    r = trace.get("route", {})
    print(f"lookup   {r.get('entity')} / {r.get('attribute')}" + (f" as of {r.get('as_of')}" if r.get("as_of") else "") + (" via hero product" if r.get("via_hero") else ""))
    if trace.get("hop1"):
        print(f"hero     {trace['hop1']['value']}  (since {trace['hop1']['valid_from']}, {trace['hop1']['source_id']})")
    hit = trace.get("hit")
    if not hit:
        print("answer   not decided (no decision recorded)"); return
    print(f"answer   {hit['value']}")
    print(f"since    {hit['valid_from']}  source {hit['source_id']}: \"{hit['source_text']}\"")
    hist = trace.get("history", [])
    prev = [h for h in hist if h["valid_to"] and h["valid_from"] < hit["valid_from"]]
    if prev:
        p = prev[-1]
        print(f"replaced {p['value']}  (held {p['valid_from']} to {p['valid_to']})")
    print(f"cost     ${usage['cost']:.6f}, {usage['in']} prompt tokens, {usage['sec']:.1f}s")


ap = argparse.ArgumentParser()
ap.add_argument("question"); ap.add_argument("--set", default="test"); ap.add_argument("--brand", required=True)
ap.add_argument("--v1", action="store_true"); ap.add_argument("--json", action="store_true")
asyncio.run(main(ap.parse_args()))
