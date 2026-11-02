"""Brand Brain: memory layer + baselines + eval runner.

Systems:
  A  frontier (Claude Opus 5.5 via `claude -p`), full brand log in context
  C  open model on Fireworks (DeepSeek V4.1 Flash), full brand log in context
  D  Brand Brain: NVIDIA Nemotron writer extracts facts into a temporal SQLite store at ingest;
     DeepSeek V4.1 Flash routes each question to a structured lookup; answers come from the store.

Usage:
  python src/bb.py ingest --set dev
  python src/bb.py eval --set dev --system D [--limit 20]
"""
import argparse, asyncio, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from generate import ALL_ATTRS, BRAND_ATTRS, SKU_ATTRS, UNDECIDED  # noqa: E402

for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

FW_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
READER = "accounts/fireworks/models/deepseek-v4p1-flash"
WRITER = "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b"
FRONTIER = "claude-opus-5-5"
# $ per 1M tokens (input, output), Fireworks list prices on 2 Nov 2026
PRICE = {READER: (0.30, 1.20), WRITER: (0.05, 0.20)}

ANSWER_RULES = (
    "Facts change over time: a later decision replaces an earlier one. Proposals, questions, ideas, "
    "competitor mentions and people recalling old values are NOT decisions. "
    "Answer with the value only (e.g. '$45', 'SAVE20', '30 days', 'Kestrel Supply'), no explanation. "
    "If the log never records a decision for what is asked, set not_decided to true and answer to an empty string. "
    'Respond in JSON: {"answer": "...", "not_decided": false}'
)


def render_log(msgs):
    return "\n".join(f"[{m['ts']}] {m['user']}: {m['text']}" for m in msgs)


# ---------------- Fireworks client ----------------
import httpx  # noqa: E402

_client = None


def fw_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=90, headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"})
    return _client


def parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


async def fw_chat(model, system, user, max_tokens=800):
    body = {"model": model, "temperature": 0, "reasoning_effort": "none", "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for attempt in range(4):
        t0 = time.time()
        try:
            r = await fw_client().post(FW_URL, json=body)
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            d = r.json()
            u = d.get("usage", {})
            pi, po = PRICE.get(model, (0, 0))
            cost = u.get("prompt_tokens", 0) / 1e6 * pi + u.get("completion_tokens", 0) / 1e6 * po
            return d["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                                                           "cost": cost, "sec": time.time() - t0}
        except httpx.HTTPError as e:
            if attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError("fireworks retries exhausted")


async def claude_chat(system, user):
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", FRONTIER, "--output-format", "json", "--system-prompt", system,
        "--tools", "", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd="/tmp")
    out, err = await asyncio.wait_for(proc.communicate(user.encode()), timeout=240)
    d = json.loads(out.decode() or "{}")
    u = d.get("usage", {})
    tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    # Opus list price assumption: $5/M in, $25/M out (stated in README)
    cost = tin / 1e6 * 5 + u.get("output_tokens", 0) / 1e6 * 25
    return d.get("result", ""), {"in": tin, "out": u.get("output_tokens", 0), "cost": cost, "sec": time.time() - t0}


# ---------------- store ----------------
def db_path(setname):
    return ROOT / "results" / f"{setname}.db"


def open_db(setname, fresh=False):
    p = db_path(setname)
    p.parent.mkdir(exist_ok=True)
    if fresh and p.exists():
        p.unlink()
    con = sqlite3.connect(p)
    con.execute("create table if not exists facts(brand text, entity text, attribute text, value text, "
                "valid_from text, valid_to text, source_id text, source_text text)")
    return con


def add_fact(con, brand, entity, attribute, value, ts, source_id, source_text):
    cur = con.execute("select rowid, value from facts where brand=? and entity=? and attribute=? and valid_to is null",
                      (brand, entity, attribute)).fetchone()
    if cur and cur[1] == value:
        return
    if cur:
        con.execute("update facts set valid_to=? where rowid=?", (ts, cur[0]))
    con.execute("insert into facts values (?,?,?,?,?,?,?,?)", (brand, entity, attribute, value, ts, None, source_id, source_text))


def lookup(con, brand, entity, attribute, as_of=None):
    """Return (current_row, history). as_of is 'YYYY-MM-DD' meaning end of that day."""
    rows = con.execute("select entity, attribute, value, valid_from, valid_to, source_id, source_text from facts "
                       "where brand=? and lower(entity)=lower(?) and attribute=? order by valid_from",
                       (brand, entity, attribute)).fetchall()
    cols = ["entity", "attribute", "value", "valid_from", "valid_to", "source_id", "source_text"]
    rows = [dict(zip(cols, r)) for r in rows]
    t = (as_of + "T23:59") if as_of else "9999"
    hit = None
    for r in rows:
        if r["valid_from"] <= t:
            hit = r
    return hit, rows


# ---------------- ingest (writer) ----------------
WRITER_SYS = (
    "You extract DECIDED business facts from a company's Slack ops log. Only extract a fact when a message "
    "states a decision or change (e.g. 'Decision:', 'confirmed', 'switching to', 'signed off', 'updating'). "
    "Ignore proposals, questions, ideas, competitor mentions and people recalling old values. "
    "entity must be the brand name for brand-level facts or the exact product name for product facts. "
    "attribute must be one of: {attrs}. Brand-level attributes: {battrs}. Product attributes: price, supplier. "
    "Copy the value exactly as written. "
    'Respond in JSON: {{"facts": [{{"msg_id": "...", "entity": "...", "attribute": "...", "value": "..."}}]}}'
)


async def ingest_brand(con, brand, data, sem, usage, batch=20):
    msgs = data["messages"]
    by_id = {m["id"]: m for m in msgs}
    system = WRITER_SYS.format(attrs=", ".join(ALL_ATTRS), battrs=", ".join(BRAND_ATTRS + UNDECIDED))
    batches = [msgs[i:i + batch] for i in range(0, len(msgs), batch)]

    async def run(b):
        user = f"Brand: {brand}. Products: {', '.join(data['skus'])}.\n\nMessages:\n" + "\n".join(
            f"{m['id']} [{m['ts']}] {m['user']}: {m['text']}" for m in b)
        async with sem:
            text, u = await fw_chat(WRITER, system, user, max_tokens=1500)
        usage.append(u)
        d = parse_json(text)
        return d.get("facts", []) if isinstance(d, dict) else []

    results = await asyncio.gather(*[run(b) for b in batches])
    facts = []
    for fs in results:
        for f in (fs if isinstance(fs, list) else []):
            if not isinstance(f, dict):
                continue
            m = by_id.get(str(f.get("msg_id")))
            if not m or f.get("attribute") not in ALL_ATTRS:
                continue
            ent = f.get("entity") or brand
            facts.append((m["ts"], m["id"], ent, f["attribute"], str(f.get("value", "")).strip(), m["text"]))
    facts.sort()  # valid_from always comes from the message timestamp, never from the model
    for ts, mid, ent, attr, val, txt in facts:
        add_fact(con, brand, ent, attr, val, ts, mid, txt)
    con.commit()
    return len(facts)


# ---------------- reader (D) ----------------
ROUTER_SYS = (
    "You route a question about a brand to a structured fact lookup. Fields:\n"
    "entity: the brand name, or a product name if the question names a product.\n"
    "attribute: one of {attrs}.\n"
    "as_of: 'YYYY-MM-DD' if the question asks about a past date, else null.\n"
    "via_hero: true if the question asks about an attribute (price, supplier) OF the hero product without naming it; "
    "then entity is the brand and attribute is the product attribute.\n"
    "Attribute meanings: promo_code=discount code; free_shipping_threshold=minimum order for free delivery; "
    "hero_sku=featured/hero product; target_customer=customer segment; daily_ad_cap=max daily ads budget; "
    "return_window=days to return; agency=ad agency; price=retail price; supplier=manufacturer; "
    "wholesale_minimum=wholesale minimum order; loyalty_discount=loyalty programme discount; "
    "tiktok_budget=TikTok budget; influencer_rate=influencer fee.\n"
    'Respond in JSON: {{"entity": "...", "attribute": "...", "as_of": null, "via_hero": false}}'
)


async def answer_D(con, brand, skus, q, sem):
    system = ROUTER_SYS.format(attrs=", ".join(ALL_ATTRS))
    user = f"Brand: {brand}. Products: {', '.join(skus)}.\nQuestion: {q['question']}"
    async with sem:
        text, u = await fw_chat(READER, system, user, max_tokens=200)
    r = parse_json(text) or {}
    ent, attr, as_of = r.get("entity") or brand, r.get("attribute"), r.get("as_of")
    trace = {"route": r}
    if r.get("via_hero"):
        hero, hist0 = lookup(con, brand, brand, "hero_sku", as_of)
        trace["hop1"] = hero
        if not hero:
            return {"answer": "", "not_decided": True}, u, trace
        ent = hero["value"]
    hit, hist = lookup(con, brand, ent, attr, as_of)
    trace["hit"], trace["history"] = hit, hist
    if not hit:
        return {"answer": "", "not_decided": True}, u, trace
    return {"answer": hit["value"], "not_decided": False}, u, trace


# ---------------- baselines (A, C) ----------------
def full_context_prompt(brand, data, q):
    return (f"Brand: {brand}. Products: {', '.join(data['skus'])}.\nOps log (oldest first):\n{render_log(data['messages'])}\n\n"
            f"Question: {q['question']}")


BASE_SYS = "You answer questions about a brand from its Slack ops log. " + ANSWER_RULES


# ---------------- grading ----------------
def norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[$,.!?'\"`]", "", s)
    s = re.sub(r"\b(usd|dollars?)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def grade(q, pred):
    if pred is None:
        return False
    nd = bool(pred.get("not_decided")) or not str(pred.get("answer") or "").strip()
    if q["answer"] is None:
        return nd
    if nd:
        return False
    g, p = norm(q["answer"]), norm(str(pred.get("answer")))
    gn, pn = re.findall(r"\d+", g), re.findall(r"\d+", p)
    if gn and re.fullmatch(r"[\d ]+(days?)?", g):
        return bool(pn) and pn[0] == gn[0]
    return g == p or g in p


# ---------------- eval ----------------
async def run_eval(setname, system, limit=None, conc=6):
    data = json.load(open(ROOT / "bench" / setname / "logs.json"))
    qs = [json.loads(l) for l in open(ROOT / "bench" / setname / "questions.jsonl")]
    if limit:
        qs = qs[:limit]
    out = ROOT / "results" / f"{setname}_{system}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in open(out)}
    qs = [q for q in qs if q["id"] not in done]
    con = open_db(setname + ("_v2" if system == "D2" else "")) if system in ("D", "D2") else None
    sem = asyncio.Semaphore(conc)
    f = open(out, "a")

    async def one(q):
        brand = q["brand"]
        try:
            if system in ("D", "D2"):
                pred, u, trace = await answer_D(con, brand, data[brand]["skus"], q, sem)
            elif system == "C":
                async with sem:
                    text, u = await fw_chat(READER, BASE_SYS, full_context_prompt(brand, data[brand], q), max_tokens=400)
                pred, trace = parse_json(text), {"raw": text}
            elif system == "A":
                async with sem:
                    text, u = await claude_chat(BASE_SYS, full_context_prompt(brand, data[brand], q))
                pred, trace = parse_json(text), {"raw": text}
        except Exception as e:
            pred, u, trace = None, {"in": 0, "out": 0, "cost": 0, "sec": 0}, {"error": repr(e)}
        rec = {**q, "system": system, "pred": pred, "correct": grade(q, pred), "usage": u, "trace": trace}
        f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        return rec

    recs = await asyncio.gather(*[one(q) for q in qs])
    n = len(recs)
    print(f"{system} on {setname}: {sum(r['correct'] for r in recs)}/{n} new")


async def run_ingest(setname, conc=8):
    data = json.load(open(ROOT / "bench" / setname / "logs.json"))
    con = open_db(setname, fresh=True)
    sem = asyncio.Semaphore(conc)
    usage = []
    t0 = time.time()
    for brand, d in data.items():
        n = await ingest_brand(con, brand, d, sem, usage)
        print(f"  {brand}: {n} facts")
    json.dump({"calls": len(usage), "in": sum(u["in"] for u in usage), "out": sum(u["out"] for u in usage),
               "cost": sum(u["cost"] for u in usage), "sec": time.time() - t0},
              open(ROOT / "results" / f"{setname}_ingest.json", "w"), indent=1)
    print("ingest cost $%.4f in %.0fs" % (sum(u["cost"] for u in usage), time.time() - t0))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--set", default="dev")
    ap.add_argument("--system")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--conc", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "ingest":
        asyncio.run(run_ingest(a.set))
    else:
        asyncio.run(run_eval(a.set, a.system, a.limit, a.conc))
