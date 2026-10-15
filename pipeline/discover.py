"""Driver: verify boards, fetch fresh postings, write candidates JSON.

Usage:
    python discover.py --agent claude --sources greenhouse,ashby,lever
    python discover.py --agent gemini --sources workday,smartrecruiters

Writes: candidates_<agent>_<YYYYMMDD-HHMM>.json  (list of normalized job dicts)
Prints a JSON summary to stdout.
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetchers import FETCHERS, load_config, verify_board  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--sources", required=True,
                    help="comma-separated: greenhouse,lever,ashby,smartrecruiters,workday")
    ap.add_argument("--hours", type=int, default=30,
                    help="lookback window; 30h gives overlap between 6h runs (dedup handles it)")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "boards.json"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    all_candidates, dead, errors = [], [], []
    for src in sources:
        if src not in FETCHERS:
            errors.append(f"unknown source: {src}")
            continue
        boards = cfg.get(src, [])
        if src == "workday":
            boards = [b for b in boards if isinstance(b, dict)]  # {base_url, board_path}
        for key in boards:
            label = key if isinstance(key, str) else key.get("board_path", key)
            ok = verify_board(src, key if isinstance(key, str) else label)
            if ok is False:
                dead.append(f"{src}/{label}")
                continue
            try:
                if src == "workday":
                    got = FETCHERS[src](key["base_url"], key["board_path"],
                                        hours=args.hours, cfg=cfg)
                else:
                    got = FETCHERS[src](key, hours=args.hours, cfg=cfg)
                all_candidates.extend(got)
            except Exception as e:  # per-board failure must not kill the run
                errors.append(f"{src}/{label}: {type(e).__name__}: {e}")

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    out_path = os.path.join(ROOT, f"candidates_{args.agent}_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(all_candidates, f, indent=1)

    print(json.dumps({
        "candidates_file": out_path,
        "fetched": len(all_candidates),
        "dead_boards": dead,
        "errors": errors,
    }, indent=1))


if __name__ == "__main__":
    main()
