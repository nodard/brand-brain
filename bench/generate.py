"""Brand Ops Bench generator. Pure random.Random(seed), no model calls.

Writes bench/<name>/logs.json (per-brand Slack-style ops logs) and bench/<name>/questions.jsonl.
Usage: python bench/generate.py --seed 1 --name dev
"""
import argparse, json, os, random
from datetime import datetime, timedelta

BRAND_NAMES = ["Juniper Skin", "Halcyon Socks", "Ferro Coffee", "Mossy Pet", "Lumen Candles", "Tidal Swim",
               "Oakline Leather", "Brightfox Kids", "Saltwater Soap", "Nimbus Sleep"]
SKU_WORDS = ["Cloud", "Ember", "River", "Atlas", "Willow", "Comet", "Harbor", "Sage", "Pebble", "Aurora", "Cedar", "Velvet"]
SKU_KINDS = ["Tee", "Serum", "Blend", "Bundle", "Kit", "Pack", "Set", "Mug", "Cream", "Tote"]
SUPPLIERS = ["Northfield Mills", "Kestrel Supply", "Bluegate Manufacturing", "Marlow & Co", "Pinecrest Labs",
             "Orion Packaging", "Redwood Fulfilment", "Coastline Goods"]
AGENCIES = ["Brightline Media", "Copperhouse", "Hatch Growth", "Northstar Ads", "Fable & Funnel", "Tandem Creative"]
SEGMENTS = ["new parents", "gen z students", "outdoor runners", "remote workers", "eco-conscious millennials",
            "busy professionals", "first-time homeowners", "gift buyers"]
PEOPLE = ["maya", "tom", "priya", "leo", "sam", "nina", "omar", "jess"]

# brand attributes that get decided and changed
BRAND_ATTRS = ["promo_code", "free_shipping_threshold", "hero_sku", "target_customer", "daily_ad_cap",
               "return_window", "agency"]
SKU_ATTRS = ["price", "supplier"]
# attributes that are discussed but never decided (abstention)
UNDECIDED = ["wholesale_minimum", "loyalty_discount", "tiktok_budget", "influencer_rate"]
ALL_ATTRS = BRAND_ATTRS + SKU_ATTRS + UNDECIDED

# how questions name each attribute (deliberately different wording from the log)
Q_NAME = {
    "promo_code": "discount code we should be advertising",
    "free_shipping_threshold": "minimum order value for free delivery",
    "hero_sku": "product we are featuring as our hero product",
    "target_customer": "customer segment we are targeting",
    "daily_ad_cap": "maximum daily paid-ads budget",
    "return_window": "number of days customers have to return an order",
    "agency": "ad agency we work with",
    "price": "retail price",
    "supplier": "manufacturer",
    "wholesale_minimum": "minimum order quantity for wholesale accounts",
    "loyalty_discount": "loyalty programme discount",
    "tiktok_budget": "monthly TikTok budget",
    "influencer_rate": "standard fee we pay influencers per post",
}


def value_for(attr, rng, brand, skus, prev=None):
    for _ in range(20):
        if attr == "promo_code":
            v = rng.choice(["SAVE", "FALL", "HELLO", "VIP", "GLOW", "WELCOME", "TREAT", "COZY"]) + str(rng.choice([10, 12, 15, 20, 25, 30]))
        elif attr == "free_shipping_threshold":
            v = "$" + str(rng.choice([35, 40, 45, 50, 55, 60, 65, 75, 80, 99]))
        elif attr == "hero_sku":
            v = rng.choice(skus)
        elif attr == "target_customer":
            v = rng.choice(SEGMENTS)
        elif attr == "daily_ad_cap":
            v = "$" + str(rng.choice([150, 200, 250, 300, 400, 500, 750, 1000, 1200]))
        elif attr == "return_window":
            v = str(rng.choice([14, 21, 30, 45, 60, 90])) + " days"
        elif attr == "agency":
            v = rng.choice(AGENCIES)
        elif attr == "price":
            v = "$" + str(rng.choice([18, 22, 24, 28, 29, 32, 34, 38, 42, 45, 49, 54, 59, 64]))
        elif attr == "supplier":
            v = rng.choice(SUPPLIERS)
        else:
            v = "$" + str(rng.choice([100, 200, 250, 500]))
        if v != prev:
            return v
    return v


def decision_text(rng, entity, attr, value, is_brand):
    subj = {"promo_code": "promo code", "free_shipping_threshold": "free shipping threshold",
            "hero_sku": "hero product", "target_customer": "target customer", "daily_ad_cap": "daily ad spend cap",
            "return_window": "return window", "agency": "agency of record", "price": f"{entity} price",
            "supplier": f"{entity} supplier"}[attr]
    t = rng.choice([
        "Decision: {s} is now {v}, effective today.",
        "ok confirmed with founders, {s} changes to {v}.",
        "Heads up team, we're switching the {s} to {v} as of now.",
        "Final call on {s}: {v}. Please update everything.",
        "Updating the {s} -> {v}. This replaces the old one.",
        "Signed off: {s} = {v}.",
    ])
    return t.format(s=subj, v=value)


def noise_text(rng, brand, skus, hist):
    kind = rng.random()
    p = rng.choice(PEOPLE)
    if kind < 0.35 and hist:
        # recollection of an OLD value (trap for "last mention wins")
        (entity, attr), vals = rng.choice(list(hist.items()))
        if len(vals) >= 2:
            old = rng.choice(vals[:-1])
            subj = {"price": f"the {entity} price", "supplier": f"the {entity} supplier"}.get(attr, attr.replace("_", " "))
            return rng.choice([
                f"remember when {subj} was {old}? that worked well tbh",
                f"a customer email still mentions {old} for {subj}, is that old?",
                f"looking at last month's deck, {subj} {old} looked good back then",
                f"someone asked if we could go back to {old} for {subj}. no decision yet",
            ])
    if kind < 0.6:
        attr = rng.choice(UNDECIDED)
        val = "$" + str(rng.choice([100, 150, 200, 250, 300, 500]))
        return rng.choice([
            f"should we set {Q_NAME[attr]} at {val}? let's discuss next week",
            f"proposal: {attr.replace('_', ' ')} around {val}. thoughts?",
            f"{p} thinks {attr.replace('_', ' ')} could be {val}, not decided",
        ])
    if kind < 0.75:
        attr = rng.choice(["promo_code", "free_shipping_threshold", "daily_ad_cap", "return_window"])
        val = value_for(attr, rng, brand, skus)
        return rng.choice([
            f"what if we tried {val} for the {attr.replace('_', ' ')}? just an idea",
            f"competitor is running {val} on {attr.replace('_', ' ')}, should we match? parking this",
        ])
    return rng.choice([
        "standup moved to 10:30 tomorrow", "who has the figma link for the new landing page?",
        "shopify app update went out, no issues", "great numbers yesterday team",
        "can someone check the klaviyo flow for abandoned carts", "photo shoot is booked for thursday",
        "reminder to log your hours", "the warehouse called about a late pallet", "lunch is on me friday",
        "new UGC batch is in the drive", "meta account rep wants a call", "PSA: wifi is down in the studio",
    ])


def gen_brand(rng, brand, start, days):
    skus = [f"{w} {k}" for w, k in zip(rng.sample(SKU_WORDS, 3), rng.sample(SKU_KINDS, 3))]
    # schedule decisions: (time, entity, attr, value)
    events = []
    keys = [(brand, a) for a in BRAND_ATTRS] + [(s, a) for s in skus for a in SKU_ATTRS]
    for entity, attr in keys:
        n_changes = rng.randint(1, 3)
        times = sorted(rng.sample(range(1, days - 1), n_changes))
        times = [rng.randint(0, 2)] + times
        prev = None
        for d in times:
            t = start + timedelta(days=d, hours=rng.randint(9, 17), minutes=rng.randint(0, 59))
            v = value_for(attr, rng, brand, skus, prev)
            prev = v
            events.append((t, entity, attr, v))
    events.sort()
    # interleave with noise
    msgs, hist, ei = [], {}, 0
    t = start
    end = start + timedelta(days=days)
    while t < end or ei < len(events):
        t = t + timedelta(minutes=rng.randint(40, 200))
        while ei < len(events) and events[ei][0] <= t:
            et, entity, attr, v = events[ei]
            msgs.append({"ts": et.strftime("%Y-%m-%dT%H:%M"), "user": rng.choice(PEOPLE),
                         "text": decision_text(rng, entity, attr, v, entity == brand)})
            hist.setdefault((entity, attr), []).append(v)
            ei += 1
        if t < end and 8 <= t.hour <= 20 and rng.random() < 0.8:
            msgs.append({"ts": t.strftime("%Y-%m-%dT%H:%M"), "user": rng.choice(PEOPLE),
                         "text": noise_text(rng, brand, skus, hist)})
    msgs.sort(key=lambda m: m["ts"])
    for i, m in enumerate(msgs):
        m["id"] = f"{brand[:3].lower()}-{i:04d}"
    return skus, events, msgs


def value_at(events, entity, attr, when):
    v = None
    for t, e, a, val in events:
        if e == entity and a == attr and t <= when:
            v = val
    return v


def changes_of(events, entity, attr):
    return [t for t, e, a, _ in events if e == entity and a == attr]


def gen_questions(rng, brand, skus, events, start, days):
    qs = []
    now = start + timedelta(days=days)
    def q(qtype, text, answer, meta):
        qs.append({"brand": brand, "type": qtype, "question": text, "answer": answer, **meta})
    # current (8)
    pool = [(brand, a) for a in BRAND_ATTRS if a != "hero_sku"] + [(brand, "hero_sku")] + [(s, a) for s in skus for a in SKU_ATTRS]
    for entity, attr in rng.sample(pool, 8):
        name = Q_NAME[attr] + (f" of the {entity}" if entity != brand else "")
        q("current", f"What is the {name} right now?", value_at(events, entity, attr, now), {"as_of": None})
    # as-of (4): pick a date with no change on that day for that key
    multi = [(e, a) for e, a in pool if len(changes_of(events, e, a)) >= 2]
    for entity, attr in rng.sample(multi, min(4, len(multi))):
        for _ in range(50):
            d = start + timedelta(days=rng.randint(4, days - 2))
            if all(c.date() != d.date() for c in changes_of(events, entity, attr)):
                break
        eod = d.replace(hour=23, minute=59)
        name = Q_NAME[attr] + (f" of the {entity}" if entity != brand else "")
        q("as_of", f"What was the {name} at the end of {d.strftime('%Y-%m-%d')}?",
          value_at(events, entity, attr, eod), {"as_of": d.strftime("%Y-%m-%d")})
    # 2-hop (4): attribute of the hero product, now or at a date
    for i in range(4):
        attr = ["price", "supplier"][i % 2]
        if i < 2:
            hero = value_at(events, brand, "hero_sku", now)
            q("two_hop", f"What is the {Q_NAME[attr]} of the {Q_NAME['hero_sku']} right now?",
              value_at(events, hero, attr, now), {"as_of": None})
        else:
            d = start + timedelta(days=rng.randint(5, days - 2))
            eod = d.replace(hour=23, minute=59)
            hero = value_at(events, brand, "hero_sku", eod)
            q("two_hop", f"At the end of {d.strftime('%Y-%m-%d')}, what was the {Q_NAME[attr]} of the {Q_NAME['hero_sku']}?",
              value_at(events, hero, attr, eod), {"as_of": d.strftime("%Y-%m-%d")})
    # abstain (4)
    for attr in UNDECIDED:
        q("abstain", f"What is our {Q_NAME[attr]}?", None, {"as_of": None})
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--brands", type=int, default=6)
    ap.add_argument("--days", type=int, default=90)
    a = ap.parse_args()
    seed = int(a.seed, 16) if not a.seed.isdigit() else int(a.seed)
    rng = random.Random(seed)
    start = datetime(2026, 8, 1)
    out = os.path.join(os.path.dirname(__file__), a.name)
    os.makedirs(out, exist_ok=True)
    logs, truth, allq = {}, {}, []
    for brand in rng.sample(BRAND_NAMES, a.brands):
        skus, events, msgs = gen_brand(rng, brand, start, a.days)
        logs[brand] = {"skus": skus, "messages": msgs}
        truth[brand] = [{"ts": t.strftime("%Y-%m-%dT%H:%M"), "entity": e, "attribute": at, "value": v} for t, e, at, v in events]
        allq += gen_questions(rng, brand, skus, events, start, a.days)
    for i, qq in enumerate(allq):
        qq["id"] = f"q{i:03d}"
    json.dump(logs, open(os.path.join(out, "logs.json"), "w"), indent=1)
    json.dump(truth, open(os.path.join(out, "truth.json"), "w"), indent=1)
    with open(os.path.join(out, "questions.jsonl"), "w") as f:
        for qq in allq:
            f.write(json.dumps(qq) + "\n")
    print(f"{a.name}: seed={a.seed} brands={len(logs)} msgs={sum(len(l['messages']) for l in logs.values())} questions={len(allq)}")


if __name__ == "__main__":
    main()
