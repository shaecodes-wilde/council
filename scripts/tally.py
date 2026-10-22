#!/usr/bin/env python3
"""Tally only accepted ballots from a frozen manifest roster.

Usage: python3 -I scripts/tally.py RUN
Writes r3/tally.json. Exit 0 means a complete election (including a valid tie);
exit 1 means incomplete/invalid voting and exit 2 means a CLI/write error.
The moderator registers the result with run.py; this helper never edits run.json.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from contracts import (accepted_paths, atomic_json, last_json, load_manifest,
                       read_json, safe_path, validate_ballot, validate_roster)


def tally(root):
    """Return a tally with no official outcome until every ballot is valid."""
    root = pathlib.Path(root).resolve()
    manifest = load_manifest(root)
    roster = manifest["vote"]["roster"]
    dimensions = [item["name"] for item in manifest["rubric"]]
    errors, missing, ballots = {}, [], {}
    out = {
        "schema_version": 1, "run_id": manifest["run_id"],
        "epoch": manifest["vote"]["epoch"], "roster": roster,
        "status": "invalid", "dimensions": dimensions,
        "min_total": len(dimensions), "max_total": 10 * len(dimensions),
        "borda_max": (len(roster) - 1) ** 2,
        "proposals": [], "top_picks": {}, "winner": None, "tied": [],
        "invalid": errors, "errors": errors, "missing": missing,
        "provenance": {}, "proposal_provenance": {},
    }
    try:
        validate_roster(manifest)
    except ValueError as exc:
        errors["roster"] = str(exc)
        return out
    # Manifest validation owns model identity and schema checks. Still enforce
    # symmetric artifact coverage here: extra files on disk cannot fill a seat.
    for stage in ("r2", "r3"):
        artifacts = manifest["accepted"].get(stage, {})
        unknown = sorted(set(artifacts) - set(roster))
        if unknown:
            errors[stage] = f"unknown accepted identities: {', '.join(unknown)}"
        for letter in roster:
            if letter not in artifacts:
                missing.append({"stage": stage, "letter": letter})
        try:
            paths = accepted_paths(root, manifest, stage)
        except (ValueError, OSError) as exc:
            errors[stage] = str(exc)
            continue
        out["provenance" if stage == "r3" else "proposal_provenance"] = {
            letter: dict(artifacts[letter]) for letter in roster if letter in paths
        }
        if stage == "r3":
            for voter in roster:
                if voter not in paths:
                    continue
                try:
                    ballots[voter] = validate_ballot(
                        last_json(paths[voter]), voter, roster, dimensions)
                except (ValueError, OSError, UnicodeError) as exc:
                    errors[voter] = str(exc)
    if errors or missing:
        out["status"] = "invalid" if errors else "incomplete"
        return out
    # Defensive against a contract implementation silently dropping artifacts.
    if set(ballots) != set(roster):
        errors["roster"] = "every member of the frozen roster needs a valid ballot"
        return out

    rows = []
    for proposal in roster:
        by = {v: sum(b["scores"][proposal][d] for d in dimensions)
              for v, b in ballots.items()}
        peers = {v: value for v, value in by.items() if v != proposal}
        peer_dims = {d: [b["scores"][proposal][d] for v, b in ballots.items()
                         if v != proposal] for d in dimensions}
        rows.append({
            "letter": proposal, "by": by, "self": by[proposal],
            "peer_count": len(peers),
            "peer_avg": round(sum(peers.values()) / len(peers), 2),
            "peer_range": [min(peers.values()), max(peers.values())],
            "peer_dim_avg": {d: round(sum(v) / len(v), 2) for d, v in peer_dims.items()},
            "peer_dim_range": {d: [min(v), max(v)] for d, v in peer_dims.items()},
            "borda": sum(len(b["ranking"]) - b["ranking"].index(proposal)
                         for b in ballots.values() if proposal in b["ranking"]),
            "first_place": sum(b["ranking"][0] == proposal for b in ballots.values()),
        })
    rows.sort(key=lambda row: (-row["borda"], row["letter"]))
    leaders = [row["letter"] for row in rows if row["borda"] == rows[0]["borda"]]
    out.update({
        "status": "complete" if len(leaders) == 1 else "tied",
        "proposals": rows,
        "top_picks": {v: b["ranking"][0] for v, b in ballots.items()},
        "winner": leaders[0] if len(leaders) == 1 else None,
        "tied": leaders if len(leaders) > 1 else [],
    })
    return out


def save_tally(root, result):
    """Keep the conventional output name without overwriting raw/accepted data."""
    root = pathlib.Path(root).resolve()
    path = safe_path(root, "r3/tally.json", exists=False)
    manifest = load_manifest(root)
    raw_paths = [safe_path(root, task["output_path"], exists=False)
                 for task in manifest["tasks"].values()]
    if path in raw_paths:
        raise ValueError("tally output collides with an immutable raw attempt")
    protected = [safe_path(root, a["path"], exists=False)
                 for stage in manifest["accepted"].values() for a in stage.values()]
    frozen = [safe_path(root, source["path"], exists=False)
              for source in manifest["sources"]]
    if path in frozen:
        raise ValueError("tally output would overwrite frozen source evidence")
    references = [manifest.get("final_verification"),
                  manifest.get("findings_provenance", {}).get("artifact")]
    protected.extend(safe_path(root, ref["path"], exists=False)
                     for ref in references if ref is not None)
    if manifest["vote"].get("tally"):
        protected.append(safe_path(root, manifest["vote"]["tally"]["path"], exists=False))
    if path in protected:
        # Repeating an identical tally is safe, but changing accepted bytes is not.
        if path.exists() and read_json(path) == result:
            return path
        raise ValueError("tally output is already accepted; invalidate it with run.py first")
    atomic_json(path, result)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        result = tally(args.run)
        path = save_tally(args.run, result)
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"tally: {exc}", file=sys.stderr)
        return 2
    print(f"{result['status']}: {path}")
    if result["winner"]:
        print(f"winner: {result['winner']}")
    elif result["tied"]:
        print(f"valid tie: {', '.join(result['tied'])}")
    for seat, error in result["errors"].items():
        print(f"INVALID {seat}: {error}", file=sys.stderr)
    for item in result["missing"]:
        print(f"MISSING {item['stage']} {item['letter']}", file=sys.stderr)
    return 0 if result["status"] in ("complete", "tied") else 1


if __name__ == "__main__":
    sys.exit(main())
