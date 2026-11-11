# werewolf-agent-01 entry: deterministic heuristic Werewolf agent (no model calls).
# Runs as python3 -P -s /bot/main.py on Python 3.13, stdlib only.
# Talks to the env at MAKEFUN_ENV_URL: POST /observe, POST /act.
# Deterministic: every choice is a function of the views the env served and
# MAKEFUN_TRIAL_SEED; no wall clock, no unseeded randomness.

import hashlib
import json
import os
import re
import sys
import time
import urllib.request

ENV_URL = os.environ.get("MAKEFUN_ENV_URL", "").rstrip("/")
SEED = os.environ.get("MAKEFUN_TRIAL_SEED", "seed")


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        ENV_URL + path, data=data,
        headers={"content-type": "application/json"}, method="POST")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=45) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1.0 + attempt)
    raise RuntimeError("unreachable")


def rng(*parts):
    h = hashlib.sha256(("agent-fun/werewolf/rng/v1|" + SEED + "|" + "|".join(str(p) for p in parts)).encode()).digest()
    return int.from_bytes(h[:8], "big")


def pick(options, *parts):
    """Deterministic pick from options."""
    if not options:
        return None
    return options[rng(*parts) % len(options)]


CLAIM_RE = re.compile(r"\b(i am|i'm|im)\s+(?:the\s+)?(seer|doctor|villager|werewolf)\b", re.I)
RESULT_RE = re.compile(r"seat\s*(\d+)\s*(?:is|was)\s*(?:a\s+)?(werewolf|not\s+a?\s*werewolf|villager|human)", re.I)


def parse_claims(view):
    """Map seat -> latest role claimed in speech."""
    claims = {}
    for e in view["log"]:
        if e.get("kind") == "speech" and e.get("text") and e["seat"] != view["you"]["seat"]:
            m = CLAIM_RE.search(e["text"])
            if m:
                claims[e["seat"]] = m.group(2).lower()
            for mr in RESULT_RE.finditer(e["text"]):
                pass  # handled elsewhere
    return claims


def accused(view):
    """Map seat -> number of times other seats accused them as werewolf."""
    acc = {}
    for e in view["log"]:
        if e.get("kind") == "speech" and e.get("text"):
            for m in re.finditer(r"seat\s*(\d+)", e["text"]):
                s = int(m.group(1))
                if s != e["seat"]:
                    acc.setdefault(s, 0)
                    if re.search(r"(werewolf|wolf|suspicious|suspicion|vote|lying|liar|evil)", e["text"][max(0, m.start() - 40):m.end() + 40], re.I):
                        acc[s] += 1
    return acc


def tally_last(view):
    """Votes cast so far today, per target."""
    t = {}
    for e in view["log"]:
        if e.get("kind") == "vote" and e.get("day") == view["day"] and e.get("target") is not None:
            t[e["target"]] = t.get(e["target"], 0) + 1
    return t


def alive_seats(view):
    return [p["seat"] for p in view["players"] if p["alive"]]


def revealed_wolves(view):
    return {p["seat"] for p in view["players"] if p["role"] == "werewolf"}


def decide(view, decision, options, max_chars):
    me = view["you"]["seat"]
    role = view["you"]["role"]
    alive = [s for s in alive_seats(view) if s != me]
    partner = view["you"]["partner"]["seat"] if view["you"].get("partner") else None
    claims = parse_claims(view)
    acc = accused(view)
    votes = tally_last(view)
    known_wolves = set(revealed_wolves(view))

    if decision == "speak":
        return speak(view, role, me, alive, partner, claims, acc, max_chars)
    if decision == "kill":
        # Options are living non-wolves. Kill a claimed seer first, then claimed doctor, else seeded.
        pri = [s for s in options if claims.get(s) == "seer"]
        if not pri:
            pri = [s for s in options if claims.get(s) == "doctor"]
        if not pri:
            # avoid the seat we accused least? just seeded pick, prefer low-accusation village seats
            pri = sorted(options, key=lambda s: (acc.get(s, 0), rng(SEED, "k", view["day"], s)))
            return {"decision": "kill", "target": pri[0]}
        return {"decision": "kill", "target": pick(pri, "kill", view["day"])}
    if decision == "vote":
        return vote(view, role, me, alive, partner, claims, acc, votes, known_wolves, options)
    if decision == "protect":
        # Protect a claimed seer (not self, not last night's), else the lowest alive other.
        opts = [s for s in options if s != me]
        last = view.get("lastProtect")
        last_seat = last["seat"] if last else None
        cands = [s for s in opts if s != last_seat]
        seers = [s for s in cands if claims.get(s) == "seer"]
        if seers:
            return {"decision": "protect", "target": seers[0]}
        if cands:
            return {"decision": "protect", "target": sorted(cands)[0]}
        return {"decision": "protect", "target": None}
    if decision == "inspect":
        # Inspect unknown, alive, non-self; prefer never-inspected, lowest seat.
        done = {r["seat"] for r in (view.get("seerResults") or [])}
        cands = [s for s in options if s not in done]
        if not cands:
            cands = options
        if cands:
            return {"decision": "inspect", "target": sorted(cands)[0]}
        return {"decision": "inspect", "target": None}
    return None


def speak(view, role, me, alive, partner, claims, acc, max_chars):
    wolf = role == "werewolf"
    known_wolves = set(revealed_wolves(view))
    if wolf:
        # Blend in: claim villager, accuse a village seat (never our partner).
        cands = [s for s in alive if s != partner and s not in known_wolves]
        if cands:
            tgt = sorted(cands, key=lambda s: (-acc.get(s, 0), rng("sp", view["day"], s)))[0] if any(acc.get(s, 0) > 0 for s in cands) else pick(cands, "sp", view["day"])
            text = "I am a villager. Seat %d keeps dodging real questions and I do not buy their story. I will be voting seat %d." % (tgt, tgt)
        else:
            text = "I am a villager. I have nothing hard yet, let us hear claims."
        return {"decision": "speak", "text": text[:max_chars]}
    # Village side.
    seer_res = view.get("seerResults") or []
    found = [r for r in seer_res if r["werewolf"]]
    if found:
        w = found[-1]["seat"]
        if w in alive:
            text = "I am the seer. I inspected seat %d at night: a werewolf. Trust me and vote seat %d out today." % (w, w)
            return {"decision": "speak", "text": text[:max_chars]}
    # Defend our claimed seer from night kills implicitly; accuse most accused seat.
    cands = [s for s in alive if acc.get(s, 0) > 0]
    if cands:
        tgt = sorted(cands, key=lambda s: (-acc.get(s, 0), s))[0]
        text = "I am a villager. Seat %d has been acting like a wolf all game, I am voting seat %d." % (tgt, tgt)
        return {"decision": "speak", "text": text[:max_chars]}
    text = "I am a villager. No hard reads yet; I want everyone's claims before we vote."
    return {"decision": "speak", "text": text[:max_chars]}


def vote(view, role, me, alive, partner, claims, acc, votes, known_wolves, options):
    opts = [s for s in options if s != me]
    wolf = role == "werewolf"
    if wolf:
        # Never vote partner; prefer bandwagoning a village seat with votes, else seeded village seat.
        cands = [s for s in opts if s != partner]
        vt = {s: votes.get(s, 0) for s in cands if s not in known_wolves}
        if vt and max(vt.values()) > 0:
            best = max(vt.values())
            tgts = sorted([s for s, v in vt.items() if v == best])
            return {"decision": "vote", "target": tgts[0]}
        cands2 = [s for s in cands if s not in known_wolves]
        if cands2:
            return {"decision": "vote", "target": pick(cands2, "v", view["day"])}
        if cands:
            return {"decision": "vote", "target": sorted(cands)[0]}
        return {"decision": "vote", "target": None}
    # Village side: seer results first.
    seer_res = view.get("seerResults") or []
    found = [r for r in seer_res if r["werewolf"]]
    for r in reversed(found):
        if r["seat"] in opts:
            return {"decision": "vote", "target": r["seat"]}
    # Follow the current plurality, but never the seat a claimed seer vouches for.
    trusted = {s for s, c in claims.items() if c == "seer"}
    if trusted:
        for s in trusted:
            if s in opts and votes.get(s, 0) == 0 and not any(v.get("seat") == s and v["werewolf"] for v in seer_res if role == "seer"):
                pass
    vt = {s: votes.get(s, 0) for s in opts if s not in trusted}
    if vt:
        best = max(vt.values())
        if best > 0:
            tgts = sorted([s for s, v in vt.items() if v == best])
            return {"decision": "vote", "target": tgts[0]}
    # No votes yet: vote the most accused non-claimed-seer seat, else seeded.
    cands = [s for s in opts if s not in trusted]
    if cands:
        scored = sorted(cands, key=lambda s: (-acc.get(s, 0), rng("v", view["day"], s)))
        return {"decision": "vote", "target": scored[0]}
    return {"decision": "vote", "target": None}


def main():
    log = lambda line: sys.stdout.write(line + "\n")
    if not ENV_URL:
        sys.stderr.write("MAKEFUN_ENV_URL missing\n")
        sys.exit(1)
    acted = {}
    decisions = 0
    for _ in range(200000):
        status, obs = post("/observe", {})
        if status != 200:
            time.sleep(1)
            continue
        if obs.get("done"):
            break
        for p in obs.get("pending", []):
            last = acted.get((p["game"], p["decision"]))
            if last == len(p["view"]["log"]):
                continue
            acted[(p["game"], p["decision"])] = len(p["view"]["log"])
            decisions += 1
            act = decide(p["view"], p["decision"], p.get("options") or [], p.get("max_chars") or 500)
            if act is None:
                act = ({"decision": "speak", "pass": True} if p["decision"] == "speak"
                       else {"decision": p["decision"], "target": None})
            act["game"] = p["game"]
            try:
                st, _body = post("/act", act)
                if st != 200:
                    acted.pop((p["game"], p["decision"]), None)
                    log("act %s game %s answered %s" % (p["decision"], p["game"], st))
            except Exception as e:
                acted.pop((p["game"], p["decision"]), None)
                log("act failed game %s: %s" % (p["game"], e))
    log("heuristic agent: %d decisions answered; done." % decisions)


if __name__ == "__main__":
    main()
