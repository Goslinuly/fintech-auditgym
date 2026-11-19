"""CLI.

    python -m auditgym.generator generate --config configs/presets/mixed_small.json --seed 7 --out /tmp/ep7
    python -m auditgym.generator build [--split train|dev|sample|all] [--check]
    python -m auditgym.generator verify episodes/train/train_00 [...]
    python -m auditgym.generator selfcheck
"""

from __future__ import annotations

import argparse
import sys

from . import generate, load_config, load_episode
from .splits import PUBLISHED_SPLITS, REPO_ROOT, build_split, check_split, iter_split
from .verify import verify_episode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m auditgym.generator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate one episode")
    g.add_argument("--config", required=True)
    g.add_argument("--seed", type=int, required=True)
    g.add_argument("--out", required=True)

    b = sub.add_parser("build", help="build published splits into episodes/")
    b.add_argument("--split", default="all", choices=(*PUBLISHED_SPLITS, "all"))
    b.add_argument("--check", action="store_true", help="only verify that regeneration is byte-identical")

    v = sub.add_parser("verify", help="verify episode directories")
    v.add_argument("dirs", nargs="+")

    sub.add_parser("selfcheck", help="generate and verify the 20-episode self-check set (not written)")

    args = ap.parse_args(argv)
    if args.cmd == "generate":
        ep = generate(args.seed, load_config(args.config))
        out = ep.write(args.out)
        print(f"{out}: {ep.episode_id}, {len(ep.truth['issues'])} issues")
        return 0

    if args.cmd == "build":
        splits = PUBLISHED_SPLITS if args.split == "all" else (args.split,)
        failed = False
        for split in splits:
            if args.check:
                problems = check_split(split)
                failed |= bool(problems)
                print(f"{split}: {'OK' if not problems else problems}")
            else:
                out = build_split(split, REPO_ROOT / "episodes")
                print(f"{split}: written to {out}")
        return 1 if failed else 0

    if args.cmd == "verify":
        failed = False
        for d in args.dirs:
            public, private = load_episode(d)
            problems = verify_episode(public, private["truth"])
            failed |= bool(problems)
            print(f"{d}: {'OK' if not problems else problems}")
        return 1 if failed else 0

    if args.cmd == "selfcheck":
        rows = []
        for item, ep in iter_split("selfcheck"):
            types = sorted({i["type"] for i in ep.truth["issues"]})
            groups = sorted({i["group"] for i in ep.truth["issues"] if i["group"]})
            rows.append(f"{item['name']}: {len(ep.truth['issues']):2d} issues {types} {groups}")
        print("\n".join(rows))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
