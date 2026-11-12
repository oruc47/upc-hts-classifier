"""
Make a clean CSV for reviewing classifications, one row per product.

Usage:
  python3 make_review.py results/gptoss120b_n100.csv --out results/review_gptoss120b_n100.csv
  python3 make_review.py results/gptoss120b_n100.csv results/qwen36_n100.csv \
      --out results/review_both_n100.csv

For each model the CSV shows the HTS-10 code, its official description (heading
down to the 10-digit line) and the general rate of duty. If Claude's codes are
available (--reference), they are shown too, with how many digits each model
agrees on (10, 8, 6, 4, 2 or 0). Two empty columns are left for reviewers.

It also prints a summary table of agreement.
"""

import argparse
import csv
import os

from hts_cascade import build_tree

HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = (10, 8, 6, 4, 2)


def dotted(number):
    """1905901085 -> 1905.90.10.85"""
    parts = [number[0:4], number[4:6], number[6:8], number[8:10]]
    return ".".join(p for p in parts if p)


def matching_digits(a, b):
    """How many leading HTS digits two codes share, in steps of 10/8/6/4/2."""
    if not a or not b:
        return ""
    for digits in LEVELS:
        if len(a) >= digits and len(b) >= digits and a[:digits] == b[:digits]:
            return digits
    return 0


class Describer:
    """Looks up the official description and duty rate of an HTS code."""

    def __init__(self, hts_path):
        self.tree = build_tree(hts_path, notes={})
        self.parent = {}
        for key, node in self.tree.items():
            for child in node["children"]:
                self.parent[child] = key
        # number -> node key (leaves win when a number appears twice)
        self.key_of = {}
        for key, node in self.tree.items():
            if node["number"] and (node["number"] not in self.key_of or not node["children"]):
                self.key_of[node["number"]] = key

    def lineage(self, number):
        """Nodes from the heading down to this code."""
        key = self.key_of.get(number)
        nodes = []
        while key and key.startswith("R:"):
            nodes.append(self.tree[key])
            key = self.parent.get(key)
        return list(reversed(nodes))

    def description(self, number):
        nodes = self.lineage(number)
        if not nodes:
            return ""
        parts = [f"{nodes[0]['code']} {nodes[0]['plain']}"] + [n["plain"] for n in nodes[1:]]
        return " > ".join(parts)

    def duty(self, number):
        """General rate of duty: the line's own rate, else the nearest line above that has one."""
        for node in reversed(self.lineage(number)):
            if node["rate"]:
                return node["rate"]
        return ""


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+", help="output file(s) of hts_cascade.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--n", type=int, default=100, help="at most this many products in the review file")
    parser.add_argument("--reference", default=os.path.join(HERE, "data", "claude_hts10_first100.csv"),
                        help="CSV with Claude's codes (column HTS10_CLAUDE); skipped if the file is missing")
    parser.add_argument("--hts", default=os.path.join(HERE, "data", "htsdata.csv"))
    args = parser.parse_args()

    describer = Describer(args.hts)

    # Model results: name -> {UPC: row}. Name = file name without the folder and .csv.
    models = {}
    for path in args.results:
        name = os.path.splitext(os.path.basename(path))[0]
        models[name] = {row["REAL_UPC"]: row for row in read_csv(path)}
    names = list(models)

    reference = {}
    if args.reference and os.path.exists(args.reference):
        for row in read_csv(args.reference):
            if row.get("HTS10_CLAUDE"):
                reference[row["REAL_UPC"]] = row["HTS10_CLAUDE"]
        print(f"Claude reference: {len(reference)} coded products")
    else:
        print("No Claude reference file; review file will not have Claude columns.")

    # Products: those in the first results file, sorted by category, at most --n.
    products = sorted(models[names[0]].values(), key=lambda r: (r["CATEGORY"], r["PROD_DESC"]))[:args.n]

    header = ["REAL_UPC", "BRAND", "CATEGORY", "PROD_DESC"]
    for name in names:
        header += [f"{name}_HTS10", f"{name}_DESCRIPTION", f"{name}_GENERAL_DUTY"]
    use_reference = any(p["REAL_UPC"] in reference for p in products)
    if use_reference:
        header += ["CLAUDE_HTS10", "CLAUDE_DESCRIPTION"]
        header += [f"{name}_DIGITS_AGREE_WITH_CLAUDE" for name in names]
    if len(names) == 2:
        header += ["DIGITS_MODELS_AGREE"]
    header += ["REVIEWER_CORRECT_HTS10", "REVIEWER_NOTES"]

    rows = []
    for product in products:
        upc = product["REAL_UPC"]
        row = [upc, product["BRAND"], product["CATEGORY"], product["PROD_DESC"]]
        codes = []
        for name in names:
            code = models[name].get(upc, {}).get("HTS10_LOCAL", "")
            codes.append(code)
            row += [dotted(code), describer.description(code), describer.duty(code)]
        if use_reference:
            claude = reference.get(upc, "")
            row += [dotted(claude), describer.description(claude)]
            row += [matching_digits(code, claude) for code in codes]
        if len(names) == 2:
            row += [matching_digits(codes[0], codes[1])]
        row += ["", ""]
        rows.append(row)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    # utf-8-sig so Excel shows characters like ¢ correctly.
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} products to {args.out}\n")

    # Summary: share of products agreeing at each level.
    comparisons = []
    if use_reference:
        for name in names:
            pairs = [(models[name].get(p["REAL_UPC"], {}).get("HTS10_LOCAL", ""), reference[p["REAL_UPC"]])
                     for p in products if p["REAL_UPC"] in reference]
            comparisons.append((f"{name} vs Claude", pairs))
    if len(names) == 2:
        pairs = [(models[names[0]].get(p["REAL_UPC"], {}).get("HTS10_LOCAL", ""),
                  models[names[1]].get(p["REAL_UPC"], {}).get("HTS10_LOCAL", "")) for p in products]
        comparisons.append((f"{names[0]} vs {names[1]}", pairs))

    if comparisons:
        print(f"{'agreement':45s} {'n':>4s}  " + " ".join(f"{'HS' + str(d):>6s}" for d in reversed(LEVELS)))
        for label, pairs in comparisons:
            shares = []
            for digits in reversed(LEVELS):
                same = sum(1 for a, b in pairs if a and b and a[:digits] == b[:digits])
                shares.append(f"{100 * same / len(pairs):5.0f}%")
            print(f"{label[:45]:45s} {len(pairs):4d}  " + " ".join(f"{s:>6s}" for s in shares))


if __name__ == "__main__":
    main()
