"""Cohort profiling for an unlabelled item master.

Answers one question before any class is authored: *what is actually in this file?* A taxonomy
built from a guess at the categories produces classes nothing matches, and classes nothing
matches are worse than no class at all — they add vocabulary that perturbs retrieval for every
class that does match something.

The method is deliberately dumb and deliberately transparent: assign each row to the first
cohort whose signal terms appear in its description, in declared order, and report what is left
over. Anything clever here would be a classifier, and a classifier is what this profiling is
supposed to inform rather than pre-empt.

Run it after adding a class to see the residue shrink:

    python scripts/profile_cohorts.py "Unihack_ Sample Dataset - Input.csv"
    python scripts/profile_cohorts.py "Unihack_ Sample Dataset - Input.csv" --residue 40
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Cohorts in priority order. The first match wins, so the more specific signal must come first:
# "cut off disc" before "disc", "sanding sponge" before "sanding".
#
# These are PROFILING probes, not the classifier's identity terms. They are allowed to be crude
# and to overlap, because their only job is to size a category so someone can decide whether it
# earns a class. The classifier's own guards are held to a much higher standard.
COHORTS: list[tuple[str, tuple[str, ...]]] = [
    # --- consumables: abrasives ------------------------------------------------------------
    ("abrasive_cutoff_wheel", ("cut off disc", "cut-off disc", "cutoff disc", "cut off wheel",
                               "cut and grind disc", "cut n grind disc", "cutting disc")),
    ("abrasive_grinding_wheel", ("grinding wheel", "grind wheel", "grinding disc")),
    ("abrasive_flap_disc", ("flap disc", "flap wheel")),
    ("abrasive_coated", ("sanding belt", "sanding disc", "sanding sheet", "sanding sponge",
                         "sandpaper", "stikit", "abranet", "hiolit", "sand disc", "grit",
                         "gr pro/", "abrasive")),
    # --- consumables: cutting -------------------------------------------------------------
    ("saw_blade", ("saw blade", "circ blade", "recip blade", "jigsaw blade", "hole saw",
                   "hole dozer", "diamond blade", "dado pro", "blade")),
    ("drill_bit", ("drill bit", "twist bit", "auger bit", "spade bit", "step bit",
                   "masonry bit", "hole cutter")),
    ("driver_bit", ("torsion bit", "driver bit", "impact bit", "screwdriver bit", "drive bit",
                    "drive - bit", "phillips", "torx", "square drive", "bit assort", "bit set",
                    "bit holder", "nut driver", "bit 5pk")),
    # --- power tools and their power system ------------------------------------------------
    ("battery_charger", ("battery", "charger", "starter kit", "power supply", "power source",
                         "jumpstart", "battery pack")),
    ("power_tool", ("bandsaw", "drill", "impact driver", "impact wrench", "angle impact",
                    "grinder", "sawzall", "circ saw", "circular saw", "miter saw", "table saw",
                    "jig saw", "jigsaw", "recip saw", "track saw", "planer", "planing machine",
                    "router", "nailer", "jointer", "lathe", "sander", "multi-tool",
                    "rotary hammer", "rotary tool", "ratchet", "rachet", "trimmer", "blower",
                    "vacuum", "dust extractor", "shaper", "stock feeder", "surge kit",
                    "hydraulic driver", "jobsite speaker")),
    ("tool_storage", ("organizer", "tool box", "packout", "tool bag", "case")),
    # --- hand tools and layout ------------------------------------------------------------
    ("layout_tool", ("mason line", "chalk", "rafter square", "rafters", "t-square", "level",
                     "caliper", "bigcal", "laser", "tape measure", "square 00")),
    ("hand_tool", ("knife", "snip", " file ", "file bstd", "wrench set", "socket set",
                   "socket adapter", "universal joint", "plier", "hammer", "pencil",
                   "voltage detector", "gauge", "holster", "t-glide", "xtender", "fence",
                   "table assembly")),
    # --- building products: decking and railing -------------------------------------------
    ("decking_board", ("decking", "deck board", "deck brd", "trex lineage", "trex enhance",
                       "trex transcend", "trex select", "trex signature", "azek", "timbertech",
                       "doug fir stk", "trex")),
    ("decking_fascia", ("fascia",)),
    ("railing_post", ("railing", "rail kit", "handrail", "baluster", "post sleeve", "post cap",
                      "post trim", "post wrap", "support post", "blank post", "gate latch",
                      "gate sq bal", "alum rail", "wall mount trex", "end cap", "deco -")),
    ("deck_accessory", ("joist tape", "deck screw", "hidden fastener", "clip", "adjust hanger")),
    # --- building products: envelope ------------------------------------------------------
    ("panel_sheathing", ("drywall", "sheathing", "zip r", "zip rainscreen", "osb", "sub floor",
                         "hardiepanel", "hardie sdg", "hardieplank", "smart lap", "smart pan",
                         "smart vented", "soffit", "rainscreen", "fine fissured")),
    ("roofing", ("premier rib", "duration", "ice guard", "shingle", "weathr lk", "eaveguard")),
    ("window_door", ("patio dr", "slider 3", "hopper", "access door", "threshold", "window",
                     "gliding patio", "casement")),
    ("masonry", ("mortar", "grout", "cement", "thinset")),
    ("tape_sealant", ("tape", "sealant", "caulk", "emseal")),
    # --- fasteners -------------------------------------------------------------------------
    ("fastener", ("screw", "bolt", " nail", "anchor", "washer", " nut ", "staple", "rivet",
                  "500ct", "bb - ")),
    # --- electrical ------------------------------------------------------------------------
    ("wire_cable", ("linear foot", "entrance cable", "so cord", "triplex", "stranded wire",
                    "cat5e", "wire 16/3")),
    ("electrical_distribution", ("load center", "load cntr", "load cnt", "breaker", "panelboard")),
    ("electrical_box", ("box cover", "oct box", "square box", "2g box", "box w/", "gfi box",
                        "wallplate", "wall plate", "decor plate", "cover wh")),
    ("wiring_device", ("gfci", "receptacle", "dimmer", "timer", "switch", "outlet", "outet",
                       "cord conn", "wall tap", "cord grip", "plug")),
    # --- lighting and fans -----------------------------------------------------------------
    ("ceiling_fan", ("fan",)),
    ("lighting", ("light", "lights", "lamp", "bulb", " led", "led ", "fluor", "flor", "sodium",
                  "halogen", "incan", " inc ", "highbay", "downlight", "luminaire", "sconce",
                  " lt", "lt ", "retro", "adj base")),
    # --- appliances ------------------------------------------------------------------------
    ("appliance_dishwasher", ("dishwasher", " dw ")),
    ("appliance_laundry", ("dryer", "washer", "laundry center")),
    ("appliance_refrigeration", ("fridge", "refrigerator", "freezer", "beverage center")),
    ("appliance_cooking", ("range", "cooktop", "wall oven", "microwave", "mocrowave",
                           "toast oven", "oven")),
    ("appliance_countertop", ("coffee maker", "espresso", "toaster", "kettle", "blender")),
    # --- safety / PPE ----------------------------------------------------------------------
    ("ppe_eyewear", ("safety glasses", "safety -", "goggle", "face shield")),
    ("ppe_apparel", ("glove", "hoodie", "jacket", "vest", "boot", "kneeling pad")),
    ("safety_device", ("fire extinguisher", "smoke", "co alarm", "alarm", "driveway alert")),
    # --- the demo vertical (absent from this dataset) --------------------------------------
    ("valve", ("valve", " vlv")),
]


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def assign(description: str) -> str:
    """First cohort whose signal appears. Substring matching, because supplier text is not
    tokenised the way a probe list is written."""
    haystack = f" {description.lower()} "
    for name, signals in COHORTS:
        for signal in signals:
            if signal in haystack:
                return name
    return "UNASSIGNED"


def head_nouns(descriptions: list[str], limit: int = 40) -> list[tuple[str, int]]:
    """Trailing words of a description, which is where this supplier puts the noun.

    "1nx6-12' Tide Pool Grooved - Trex Enhance Basics Decking" — the product kind is the last
    token, not the first. Leading tokens are part numbers.
    """
    counts: Counter[str] = Counter()
    for description in descriptions:
        words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", description)
        if words:
            counts[words[-1].lower()] += 1
    return counts.most_common(limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--column", default="Part_Desc")
    parser.add_argument(
        "--residue", type=int, default=25, help="how many unassigned rows to print"
    )
    parser.add_argument(
        "--against-schema",
        action="store_true",
        help="also report what the live classifier does with each cohort",
    )
    args = parser.parse_args(argv)

    if not args.csv_path.is_file():
        print(f"not found: {args.csv_path}", file=sys.stderr)
        return 2

    with open(args.csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if args.column not in (rows[0] if rows else {}):
        print(f"column {args.column!r} not in {list(rows[0])}", file=sys.stderr)
        return 2

    buckets: dict[str, list[str]] = {}
    for row in rows:
        description = (row.get(args.column) or "").strip()
        buckets.setdefault(assign(description), []).append(description)

    total = len(rows)
    print(f"{total} rows from {args.csv_path.name}\n")
    print(f"{'cohort':<26} {'rows':>5} {'share':>7}")
    print("-" * 42)
    for name, members in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        share = len(members) / total * 100
        print(f"{name:<26} {len(members):>5} {share:>6.1f}%")

    assigned = total - len(buckets.get("UNASSIGNED", []))
    print("-" * 42)
    print(f"{'assigned':<26} {assigned:>5} {assigned / total * 100:>6.1f}%")

    residue = buckets.get("UNASSIGNED", [])
    if residue:
        print(f"\nunassigned head nouns (top 40 of {len(residue)} rows):")
        for noun, count in head_nouns(residue):
            print(f"  {count:>4}  {noun}")
        print(f"\nunassigned sample ({min(args.residue, len(residue))} of {len(residue)}):")
        for description in residue[: args.residue]:
            print(f"  {description}")

    if args.against_schema:
        _report_against_schema(buckets)
    return 0


def _report_against_schema(buckets: dict[str, list[str]]) -> None:
    """What the live classifier currently does with each cohort.

    Kept behind a flag so the profiling itself has no dependency on the schema loading cleanly —
    the whole point is to be able to run this while the schema is mid-edit.
    """
    from axiom.classify.candidates import CandidateIndex
    from axiom.schema import load_default

    index = CandidateIndex.build(load_default())
    print("\n\nagainst the live schema")
    print(f"{'cohort':<26} {'rows':>5} {'top-1 class':>32} {'hit':>6}")
    print("-" * 74)
    for name, members in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        winners: Counter[str] = Counter()
        for description in members:
            ranked = index.search(description)
            winners[ranked[0].code if ranked else "(none)"] += 1
        top, count = winners.most_common(1)[0]
        covered = sum(v for k, v in winners.items() if k != "(none)")
        print(f"{name:<26} {len(members):>5} {top:>32} {covered / len(members):>5.0%}")


if __name__ == "__main__":
    raise SystemExit(main())
