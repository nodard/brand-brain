"""v2: verify pass. Each extracted fact's source message is checked by the reader model:
is this message a DECISION/CHANGE, or a proposal / question / recollection of an old value?
Non-decisions are dropped and the temporal store is rebuilt. Usage: python src/verify.py --set test"""
import argparse, asyncio, json, sqlite3, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import bb

SYS = ("Classify one Slack message from a company ops channel. decision=true only if the message announces "
       "a decision or change that takes effect (e.g. confirmed, switching to, signed off, updating, final call). "
       "decision=false for proposals, ideas, questions, 'not decided', competitor mentions, or people remembering "
       "or quoting an old/previous value. Respond in JSON: {\"decision\": true}")

async def main(setname):
    con = sqlite3.connect(bb.db_path(setname))
    rows = con.execute("select * from facts").fetchall()
    texts = sorted({r[7] for r in rows})
    sem = asyncio.Semaphore(16); usage = []; keep = {}
    async def one(t):
        async with sem:
            out, u = await bb.fw_chat(bb.READER, SYS, t, max_tokens=30)
        usage.append(u); d = bb.parse_json(out) or {}
        keep[t] = bool(d.get("decision"))
    await asyncio.gather(*[one(t) for t in texts])
    rows = [r for r in rows if keep.get(r[7])]
    con.close()
    con = bb.open_db(setname + "_v2", fresh=True)
    for r in sorted(rows, key=lambda r: r[4]):
        bb.add_fact(con, r[0], r[1], r[2], r[3], r[4], r[6], r[7])
    con.commit()
    ing = json.load(open(bb.ROOT / "results" / f"{setname}_ingest.json"))
    ing["verify_cost"] = sum(u["cost"] for u in usage); ing["verify_calls"] = len(usage)
    json.dump(ing, open(bb.ROOT / "results" / f"{setname}_v2_ingest.json", "w"), indent=1)
    print(f"kept {len(rows)} facts; verify cost ${ing['verify_cost']:.4f}")

ap = argparse.ArgumentParser(); ap.add_argument("--set", default="test"); asyncio.run(main(ap.parse_args().set))
