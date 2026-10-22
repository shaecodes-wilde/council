#!/usr/bin/env python3
"""Tally round-3 ballots: peer scores per proposal and a Borda count.

usage: tally.py <debate-dir>

Proposals = letters with an r2/<L>.md answer. Voters = letters with an r3/<L>.md.
Each ballot is the last ```json block of the voter's file:
  {"scores": {"A": {"dim": 8, ...}, ...}, "ranking": ["C", "A", ...], ...}
A ballot must rank every other proposal exactly once and never its own letter.
Invalid ballots are reported and left out. Writes r3/tally.json.
Borda: with m letters ranked, 1st gets m points, last gets 1.
"""
import json
import pathlib
import re
import sys

JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def last_json(path: pathlib.Path):
    blocks = JSON_BLOCK.findall(path.read_text(encoding="utf-8"))
    if not blocks:
        raise ValueError("no ```json block")
    return json.loads(blocks[-1])


def total(dims: dict) -> float:
    return round(sum(float(v) for v in dims.values()), 2)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = pathlib.Path(sys.argv[1])
    proposals = sorted(p.stem for p in (root / "r2").glob("*.md"))
    voters = sorted(p.stem for p in (root / "r3").glob("*.md"))
    if not proposals or not voters:
        print("need r2/*.md and r3/*.md")
        return 1

    ballots, invalid = {}, {}
    for v in voters:
        try:
            b = last_json(root / "r3" / f"{v}.md")
            expected = sorted(p for p in proposals if p != v)
            ranking = b.get("ranking", [])
            if v in ranking:
                raise ValueError("ranks its own proposal")
            if sorted(ranking) != expected:
                raise ValueError(f"ranking {ranking} must contain exactly {expected}")
            if not isinstance(b.get("scores"), dict):
                raise ValueError("missing scores")
            ballots[v] = b
        except (ValueError, json.JSONDecodeError) as e:
            invalid[v] = str(e)

    dims = []
    for b in ballots.values():
        for s in b["scores"].values():
            for d in s:
                if d not in dims:
                    dims.append(d)

    rows = []
    for p in proposals:
        by = {}
        for v, b in ballots.items():
            s = b["scores"].get(p)
            if s:
                by[v] = {"total": total(s), "dims": s}
        peers = {v: x for v, x in by.items() if v != p}
        peer_dim_avg = {}
        for d in dims:
            vals = [float(x["dims"][d]) for x in peers.values() if d in x["dims"]]
            if vals:
                peer_dim_avg[d] = round(sum(vals) / len(vals), 2)
        borda, firsts = 0, 0
        for v, b in ballots.items():
            r = b["ranking"]
            if p in r:
                borda += len(r) - r.index(p)
                firsts += r.index(p) == 0
        rows.append({
            "letter": p,
            "by": {v: x["total"] for v, x in by.items()},
            "self": by.get(p, {}).get("total"),
            "peer_avg": round(sum(x["total"] for x in peers.values()) / len(peers), 2) if peers else None,
            "peer_dim_avg": peer_dim_avg,
            "borda": borda,
            "first_place": firsts,
        })

    rows.sort(key=lambda r: (-r["borda"], r["letter"]))
    top = rows[0]["borda"]
    leaders = [r["letter"] for r in rows if r["borda"] == top]
    out = {
        "dimensions": dims,
        "max_total": 10 * len(dims),
        "proposals": rows,
        "top_picks": {v: b["ranking"][0] for v, b in ballots.items()},
        "winner": leaders[0] if len(leaders) == 1 else None,
        "tied": leaders if len(leaders) > 1 else [],
        "invalid": invalid,
    }
    (root / "r3" / "tally.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    for r in rows:
        print(f"{r['letter']}: Borda {r['borda']}, 1st-place {r['first_place']}, "
              f"peer avg {r['peer_avg']}/{out['max_total']}, self {r['self']}")
    print("winner:", out["winner"] or f"TIE between {', '.join(leaders)}")
    for v, e in invalid.items():
        print(f"INVALID ballot {v}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
