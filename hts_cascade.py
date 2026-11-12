"""
Classify retail products into HTS-10 codes by walking down the HTS tree one
level at a time, following Cavallo et al., Appendix B.2.1:

  section -> chapter -> heading (4-digit) -> subheading (6) -> ... -> 10-digit

At each step the model sees the product and the immediate subcategories of the
node chosen so far, and must return exactly one code from that list. Nodes with
a single child are passed through without asking. The walk stops at a leaf,
which is normally a 10-digit statistical line.

Labels can be enriched with clarifying notes from hts_notes.csv (paper: "we
augment the original HS hierarchy with enriched labels and clarifying
annotations").

Every step (options shown and option chosen) is saved to <output>.steps.jsonl,
so the ambiguity analysis of Appendix B.2.2 can be done afterwards.

Usage:
  python3 hts_cascade.py --input all_items.csv --output all_qwen36.csv

(Defaults: model qwen3.6:35b-a3b-q8_0 with reasoning off, 8 parallel requests.)

The input file needs a UPC column and a description column (e.g. upc / prod_desc
or REAL_UPC / PROD_DESC, any case, comma- or tab-separated). Brand and category are
used if present, unless --desc-only. A stopped run continues where it left off when the
same command is run again.
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
HERE = os.path.dirname(os.path.abspath(__file__))

# Harmonized System sections: (roman numeral, title, first chapter, last chapter).
SECTIONS = [
    ("I", "Live animals; animal products", 1, 5),
    ("II", "Vegetable products", 6, 14),
    ("III", "Animal, vegetable or microbial fats and oils; prepared edible fats; animal or vegetable waxes", 15, 15),
    ("IV", "Prepared foodstuffs; beverages, spirits and vinegar; tobacco and manufactured tobacco substitutes; nicotine products", 16, 24),
    ("V", "Mineral products", 25, 27),
    ("VI", "Products of the chemical or allied industries", 28, 38),
    ("VII", "Plastics and articles thereof; rubber and articles thereof", 39, 40),
    ("VIII", "Raw hides and skins, leather, furskins and articles thereof; saddlery and harness; travel goods, handbags and similar containers", 41, 43),
    ("IX", "Wood and articles of wood; wood charcoal; cork; manufactures of straw; basketware", 44, 46),
    ("X", "Pulp of wood; recovered paper; paper and paperboard and articles thereof", 47, 49),
    ("XI", "Textiles and textile articles", 50, 63),
    ("XII", "Footwear, headgear, umbrellas, walking-sticks; prepared feathers; artificial flowers; articles of human hair", 64, 67),
    ("XIII", "Articles of stone, plaster, cement, asbestos, mica or similar materials; ceramic products; glass and glassware", 68, 70),
    ("XIV", "Natural or cultured pearls, precious stones, precious metals; imitation jewelry; coin", 71, 71),
    ("XV", "Base metals and articles of base metal", 72, 83),
    ("XVI", "Machinery and mechanical appliances; electrical equipment; sound and television recorders and reproducers", 84, 85),
    ("XVII", "Vehicles, aircraft, vessels and associated transport equipment", 86, 89),
    ("XVIII", "Optical, photographic, measuring, medical or surgical instruments; clocks and watches; musical instruments", 90, 92),
    ("XIX", "Arms and ammunition", 93, 93),
    ("XX", "Miscellaneous manufactured articles", 94, 96),
    ("XXI", "Works of art, collectors' pieces and antiques", 97, 97),
]

# Harmonized System chapter titles. Chapters 98-99 (special provisions,
# temporary modifications) are not product classifications and are left out.
CHAPTER_TITLES = {
    "01": "Live animals",
    "02": "Meat and edible meat offal",
    "03": "Fish and crustaceans, molluscs and other aquatic invertebrates",
    "04": "Dairy produce; birds' eggs; natural honey; edible products of animal origin, not elsewhere specified or included",
    "05": "Products of animal origin, not elsewhere specified or included",
    "06": "Live trees and other plants; bulbs, roots and the like; cut flowers and ornamental foliage",
    "07": "Edible vegetables and certain roots and tubers",
    "08": "Edible fruit and nuts; peel of citrus fruit or melons",
    "09": "Coffee, tea, mate and spices",
    "10": "Cereals",
    "11": "Products of the milling industry; malt; starches; inulin; wheat gluten",
    "12": "Oil seeds and oleaginous fruits; miscellaneous grains, seeds and fruit; industrial or medicinal plants; straw and fodder",
    "13": "Lac; gums, resins and other vegetable saps and extracts",
    "14": "Vegetable plaiting materials; vegetable products not elsewhere specified or included",
    "15": "Animal, vegetable or microbial fats and oils and their cleavage products; prepared edible fats; animal or vegetable waxes",
    "16": "Preparations of meat, of fish, of crustaceans, molluscs or other aquatic invertebrates, or of insects",
    "17": "Sugars and sugar confectionery",
    "18": "Cocoa and cocoa preparations",
    "19": "Preparations of cereals, flour, starch or milk; bakers' wares",
    "20": "Preparations of vegetables, fruit, nuts or other parts of plants",
    "21": "Miscellaneous edible preparations",
    "22": "Beverages, spirits and vinegar",
    "23": "Residues and waste from the food industries; prepared animal feed",
    "24": "Tobacco and manufactured tobacco substitutes; products for inhalation without combustion; other nicotine products",
    "25": "Salt; sulfur; earths and stone; plastering materials, lime and cement",
    "26": "Ores, slag and ash",
    "27": "Mineral fuels, mineral oils and products of their distillation; bituminous substances; mineral waxes",
    "28": "Inorganic chemicals; organic or inorganic compounds of precious metals, of rare-earth metals, of radioactive elements or of isotopes",
    "29": "Organic chemicals",
    "30": "Pharmaceutical products",
    "31": "Fertilizers",
    "32": "Tanning or dyeing extracts; tannins and their derivatives; dyes, pigments and other coloring matter; paints and varnishes; putty and other mastics; inks",
    "33": "Essential oils and resinoids; perfumery, cosmetic or toilet preparations",
    "34": "Soap, organic surface-active agents, washing preparations, lubricating preparations, artificial waxes, prepared waxes, polishing or scouring preparations, candles and similar articles, modeling pastes, dental waxes and dental preparations with a basis of plaster",
    "35": "Albuminoidal substances; modified starches; glues; enzymes",
    "36": "Explosives; pyrotechnic products; matches; pyrophoric alloys; certain combustible preparations",
    "37": "Photographic or cinematographic goods",
    "38": "Miscellaneous chemical products",
    "39": "Plastics and articles thereof",
    "40": "Rubber and articles thereof",
    "41": "Raw hides and skins (other than furskins) and leather",
    "42": "Articles of leather; saddlery and harness; travel goods, handbags and similar containers; articles of animal gut",
    "43": "Furskins and artificial fur; manufactures thereof",
    "44": "Wood and articles of wood; wood charcoal",
    "45": "Cork and articles of cork",
    "46": "Manufactures of straw, of esparto or of other plaiting materials; basketware and wickerwork",
    "47": "Pulp of wood or of other fibrous cellulosic material; recovered (waste and scrap) paper or paperboard",
    "48": "Paper and paperboard; articles of paper pulp, of paper or of paperboard",
    "49": "Printed books, newspapers, pictures and other products of the printing industry; manuscripts, typescripts and plans",
    "50": "Silk",
    "51": "Wool, fine or coarse animal hair; horsehair yarn and woven fabric",
    "52": "Cotton",
    "53": "Other vegetable textile fibers; paper yarn and woven fabrics of paper yarn",
    "54": "Man-made filaments; strip and the like of man-made textile materials",
    "55": "Man-made staple fibers",
    "56": "Wadding, felt and nonwovens; special yarns; twine, cordage, ropes and cables and articles thereof",
    "57": "Carpets and other textile floor coverings",
    "58": "Special woven fabrics; tufted textile fabrics; lace; tapestries; trimmings; embroidery",
    "59": "Impregnated, coated, covered or laminated textile fabrics; textile articles of a kind suitable for industrial use",
    "60": "Knitted or crocheted fabrics",
    "61": "Articles of apparel and clothing accessories, knitted or crocheted",
    "62": "Articles of apparel and clothing accessories, not knitted or crocheted",
    "63": "Other made up textile articles; needlecraft sets; worn clothing and worn textile articles; rags",
    "64": "Footwear, gaiters and the like; parts of such articles",
    "65": "Headgear and parts thereof",
    "66": "Umbrellas, sun umbrellas, walking-sticks, seat-sticks, whips, riding-crops and parts thereof",
    "67": "Prepared feathers and down and articles made of feathers or of down; artificial flowers; articles of human hair",
    "68": "Articles of stone, plaster, cement, asbestos, mica or similar materials",
    "69": "Ceramic products",
    "70": "Glass and glassware",
    "71": "Natural or cultured pearls, precious or semi-precious stones, precious metals, metals clad with precious metal, and articles thereof; imitation jewelry; coin",
    "72": "Iron and steel",
    "73": "Articles of iron or steel",
    "74": "Copper and articles thereof",
    "75": "Nickel and articles thereof",
    "76": "Aluminum and articles thereof",
    "78": "Lead and articles thereof",
    "79": "Zinc and articles thereof",
    "80": "Tin and articles thereof",
    "81": "Other base metals; cermets; articles thereof",
    "82": "Tools, implements, cutlery, spoons and forks, of base metal; parts thereof of base metal",
    "83": "Miscellaneous articles of base metal",
    "84": "Nuclear reactors, boilers, machinery and mechanical appliances; parts thereof",
    "85": "Electrical machinery and equipment and parts thereof; sound recorders and reproducers, television image and sound recorders and reproducers, and parts and accessories of such articles",
    "86": "Railway or tramway locomotives, rolling stock and parts thereof; railway or tramway track fixtures and fittings; mechanical traffic signaling equipment",
    "87": "Vehicles other than railway or tramway rolling stock, and parts and accessories thereof",
    "88": "Aircraft, spacecraft, and parts thereof",
    "89": "Ships, boats and floating structures",
    "90": "Optical, photographic, cinematographic, measuring, checking, precision, medical or surgical instruments and apparatus; parts and accessories thereof",
    "91": "Clocks and watches and parts thereof",
    "92": "Musical instruments; parts and accessories of such articles",
    "93": "Arms and ammunition; parts and accessories thereof",
    "94": "Furniture; bedding, mattresses, mattress supports, cushions and similar stuffed furnishings; luminaires and lighting fittings, not elsewhere specified or included; illuminated signs, illuminated nameplates and the like; prefabricated buildings",
    "95": "Toys, games and sports requisites; parts and accessories thereof",
    "96": "Miscellaneous manufactured articles",
    "97": "Works of art, collectors' pieces and antiques",
}


# ---------------------------------------------------------------------------
# Building the tree
# ---------------------------------------------------------------------------

def clean(text):
    """Remove HTML tags (e.g. <i>) and extra spaces from an HTS description."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def load_notes(path):
    """Clarifying notes keyed by 'section IV', 'chapter 19' or an HTS number like '1905' or '2202.99'."""
    notes = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row["key"].strip() and row["note"].strip():
                    notes[row["key"].strip()] = row["note"].strip()
    return notes


def build_tree(hts_path, notes):
    """Return a dict of nodes. Each node has:
        code     - what the model sees and returns ("IV", "19", "1905", "1905.90.10", ...)
        label    - description shown next to the code (with any clarifying note)
        plain    - the official description alone (no note)
        number   - HTS digits without dots ("" for sections, chapters, unnumbered groups)
        rate     - general rate of duty on that line ("" if none)
        children - keys of the child nodes, in schedule order
    Keys: "ROOT", "S:IV" (sections), "C:19" (chapters), "R:<row number>" (HTS rows).
    """
    tree = {"ROOT": {"code": "", "label": "Harmonized Tariff Schedule", "plain": "", "number": "",
                     "rate": "", "children": []}}

    for roman, title, first, last in SECTIONS:
        label = title
        if notes.get(f"section {roman}"):
            label += f" [Note: {notes[f'section {roman}']}]"
        tree["S:" + roman] = {"code": roman, "label": label, "plain": title, "number": "",
                              "rate": "", "children": []}
        tree["ROOT"]["children"].append("S:" + roman)
        for chapter_number in range(first, last + 1):
            ch = f"{chapter_number:02d}"
            if ch not in CHAPTER_TITLES:
                continue
            label = CHAPTER_TITLES[ch]
            if notes.get(f"chapter {ch}"):
                label += f" [Note: {notes[f'chapter {ch}']}]"
            tree["C:" + ch] = {"code": ch, "label": label, "plain": CHAPTER_TITLES[ch], "number": "",
                               "rate": "", "children": []}
            tree["S:" + roman]["children"].append("C:" + ch)

    with open(hts_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    stack = []  # (indent, key) of the open rows above the current one
    for i, row in enumerate(rows):
        indent = int(row["Indent"]) if row["Indent"].strip().isdigit() else 0
        written = row["HTS Number"].strip()
        number = written.replace(".", "")
        key = f"R:{i}"
        plain = clean(row["Description"])
        label = plain
        if notes.get(written):
            label += f" [Note: {notes[written]}]"
        node = {"code": written, "label": label, "plain": plain, "number": number,
                "rate": row["General Rate of Duty"].strip(), "children": []}

        if indent == 0:
            stack = []
            # Indent-0 rows are headings. Unnumbered indent-0 rows are
            # subchapter titles (e.g. in chapter 28); skip them.
            if not number or "C:" + number[:2] not in tree:
                continue
            tree[key] = node
            tree["C:" + number[:2]]["children"].append(key)
            stack = [(0, key)]
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            continue  # row outside any heading we keep (chapters 98-99)
        tree[key] = node
        tree[stack[-1][1]]["children"].append(key)
        stack.append((indent, key))

    return tree


# ---------------------------------------------------------------------------
# Talking to Ollama
# ---------------------------------------------------------------------------

SETTINGS = {"model": "", "think": "off", "num_ctx": 16384}


def parse_json_reply(text):
    """Parse the model's reply; tolerate extra text around the JSON object."""
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    return None


def ask_ollama(prompt, allowed_codes, timeout=300):
    """Ask for one code from allowed_codes. Returns the code, or None after 3 failed tries."""
    schema = {
        "type": "object",
        "properties": {"code": {"type": "string", "enum": allowed_codes}},
        "required": ["code"],
    }
    body = {
        "model": SETTINGS["model"],
        "messages": [{"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        # Same context size on every request; if it changes, Ollama reloads the model.
        "options": {"temperature": 0, "num_ctx": SETTINGS["num_ctx"]},
        "think": False if SETTINGS["think"] == "off" else SETTINGS["think"],
    }
    for attempt in range(3):
        try:
            data = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(OLLAMA_URL, data=data,
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.loads(response.read())
            text = reply["message"]["content"]
            answer = parse_json_reply(text)
            if answer is not None:
                code = str(answer.get("code", "")).strip()
            else:
                # Malformed JSON: look for "code": "..." in the text.
                match = re.search(r'"code"\s*:\s*"([^"]+)"', text)
                code = match.group(1).strip() if match else ""
            if code in allowed_codes:
                return code
            print("  unusable reply:", reply["message"]["content"][:150])
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", "replace")
            if "think" in message.lower() and "think" in body:
                del body["think"]  # model doesn't accept the think setting
                continue
            print("  Ollama error:", message[:200])
        except Exception as error:  # timeouts, server restarts
            print("  Ollama call failed:", repr(error)[:200])
        time.sleep(2)
    return None


def load_model():
    """Load the model before starting, report how long it took, and warn if it
    doesn't fit in GPU memory (then it runs mostly on the CPU and is very slow)."""
    print(f"Loading {SETTINGS['model']} into memory (can take a few minutes the first time)...")
    start = time.time()
    if ask_ollama('Reply with {"code": "OK"}', ["OK"], timeout=1800) is None:
        sys.exit("The model did not answer. Look at the terminal running 'ollama serve' for an error.")
    print(f"Model loaded and answering after {time.time() - start:.0f} s")

    try:
        url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/ps"
        with urllib.request.urlopen(url, timeout=30) as response:
            running = json.loads(response.read()).get("models", [])
    except Exception:
        return  # only a check; carry on if it fails
    for model in running:
        if model.get("name") != SETTINGS["model"] and model.get("model") != SETTINGS["model"]:
            continue
        size, on_gpu = model.get("size", 0), model.get("size_vram", 0)
        if size and on_gpu < 0.99 * size:
            print(f"\nWARNING: only {100 * on_gpu / size:.0f}% of the model ({size / 1e9:.0f} GB) is in GPU "
                  "memory. The rest runs on the CPU, which is very slow. This Mac probably does not "
                  "have enough memory for this model, or another model is still loaded "
                  "(run 'ollama ps' to check, 'ollama stop <model>' to unload).\n")


# ---------------------------------------------------------------------------
# Walking down the tree
# ---------------------------------------------------------------------------

def shown_codes(tree, child_keys):
    """The code shown for each child. Unnumbered groups (e.g. 'Horses:') get G1, G2, ..."""
    codes = []
    group = 0
    for key in child_keys:
        code = tree[key]["code"]
        if not code:
            group += 1
            code = f"G{group}"
        codes.append(code)
    return codes


def classify(tree, item_text):
    """Walk from the root to a leaf. Returns (final code, path, steps, number of model calls).

    final code is "" if the model failed to answer at some step.
    """
    key = "ROOT"
    path = []    # labels chosen so far, shown to the model as context
    steps = []   # one entry per step, for the ambiguity analysis
    calls = 0

    while tree[key]["children"]:
        children = tree[key]["children"]
        codes = shown_codes(tree, children)

        if len(children) == 1:
            chosen_key = children[0]  # nothing to choose
        else:
            lines = [f"{code}: {tree[child]['label']}" for code, child in zip(codes, children)]
            context = ""
            if path:
                context = "The item has already been classified as: " + " > ".join(path) + "\n\n"
            # The shared part (context and list) comes first and the item last, so
            # products at the same node share a prompt prefix that Ollama can reuse.
            prompt = (
                context
                + "Classify the following item into one of these subcategories:\n"
                + "\n".join(lines)
                + f'\n\nItem: "{item_text}"\n'
                + 'Return exactly one code from the list. Reply as JSON: {"code": "<code>"}'
            )
            answer = ask_ollama(prompt, codes)
            calls += 1
            if answer is None:
                return "", path, steps, calls
            chosen_key = children[codes.index(answer)]

        steps.append({"node": key, "options": children, "chosen": chosen_key})
        chosen = tree[chosen_key]
        path.append(f"{chosen['code'] or 'group'}: {chosen['plain'][:150]}")  # context without our notes
        key = chosen_key

    # The leaf is normally a 10-digit line. If it is an unnumbered group, use
    # the nearest numbered node above it.
    final = tree[key]["number"]
    for step in reversed(steps):
        if final:
            break
        final = tree[step["chosen"]]["number"]
    return final, path, steps, calls


# ---------------------------------------------------------------------------
# Reading product files
# ---------------------------------------------------------------------------

# Accepted column names (compared after upper-casing and turning spaces and
# dashes into underscores) -> the name used in this code.
COLUMN_NAMES = {
    "REAL_UPC": "REAL_UPC", "UPC": "REAL_UPC", "UPC_CODE": "REAL_UPC",
    "PROD_DESC": "PROD_DESC", "PRODUCT_DESCRIPTION": "PROD_DESC", "DESCRIPTION": "PROD_DESC",
    "DESC": "PROD_DESC", "UPC_DESCR": "PROD_DESC", "UPC_DESCRIPTION": "PROD_DESC",
    "BRAND": "BRAND", "BRAND_DESCR": "BRAND", "BRAND_NAME": "BRAND",
    "CATEGORY": "CATEGORY", "CATEGORY_DESCR": "CATEGORY", "PRODUCT_CATEGORY": "CATEGORY",
    "COMPANY": "COMPANY", "MANUFACTURER": "COMPANY",
}


def read_products(path, required=("REAL_UPC", "PROD_DESC")):
    """Read a product file into a list of dicts with standard column names.

    Accepts comma-, tab-, semicolon- or bar-separated files, column names in any
    case (e.g. "upc", "prod_desc"), and files saved by Excel in other encodings.
    BRAND, CATEGORY and COMPANY are set to "" when the file doesn't have them.
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")          # Excel "Unicode Text"
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")      # older Excel CSV on Windows/Mac

    lines = text.splitlines()
    if not lines:
        raise SystemExit(f"{path} is empty")
    try:
        delimiter = csv.Sniffer().sniff(lines[0], delimiters=",\t;|").delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(lines, delimiter=delimiter)
    header = next(reader)
    names = []
    for column in header:
        simple = re.sub(r"[\s\-]+", "_", column.strip()).upper()
        names.append(COLUMN_NAMES.get(simple, simple))

    missing = [name for name in required if name not in names]
    if missing:
        raise SystemExit(
            f"{path}: missing column(s) {', '.join(missing)}.\n"
            f"Columns found: {', '.join(header)}\n"
            "Needed: a UPC column (e.g. upc or REAL_UPC) and a description column "
            "(e.g. prod_desc or PROD_DESC); brand and category are optional."
        )

    products = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue  # skip blank lines
        row = {name: value.strip() for name, value in zip(names, values)}
        for name in ("BRAND", "CATEGORY", "COMPANY"):
            row.setdefault(name, "")
        products.append(row)
    return products


def item_text_for(item, desc_only):
    if desc_only or not item.get("BRAND"):
        return item["PROD_DESC"]
    text = f"{item['PROD_DESC']} (brand: {item['BRAND']}"
    if item.get("CATEGORY"):
        text += f"; retail category: {item['CATEGORY']}"
    return text + ")"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.6:35b-a3b-q8_0", help="Ollama model tag")
    parser.add_argument("--think", default="off", choices=["off", "low", "medium", "high"],
                        help="reasoning effort; gpt-oss cannot be 'off', use 'low'")
    parser.add_argument("--num-ctx", type=int, default=16384, help="context window (same for every request)")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--hts", default=os.path.join(HERE, "data", "htsdata.csv"))
    parser.add_argument("--notes", default=os.path.join(HERE, "hts_notes.csv"),
                        help="clarifying notes added to labels ('' to use the plain HTS text)")
    parser.add_argument("--desc-only", action="store_true",
                        help="show only PROD_DESC to the model (no brand or retail category)")
    parser.add_argument("--limit", type=int, default=0, help="only do the first N products")
    parser.add_argument("--sample", type=int, default=0, help="only do a random sample of N products")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=8,
                        help="parallel requests; start Ollama with OLLAMA_NUM_PARALLEL set to the same number")
    parser.add_argument("--project", type=int, default=300000,
                        help="print the projected run time for this many products")
    args = parser.parse_args()

    SETTINGS["model"] = args.model
    SETTINGS["think"] = args.think
    SETTINGS["num_ctx"] = args.num_ctx
    # Print each line right away, even when the output goes through tee into a log file.
    sys.stdout.reconfigure(line_buffering=True)

    notes = load_notes(args.notes)
    tree = build_tree(args.hts, notes)
    leaves = sum(1 for node in tree.values() if not node["children"])
    print(f"HTS tree: {len(tree)} nodes, {leaves} leaves, {len(notes)} clarifying notes")

    items = read_products(args.input)
    if args.sample and args.sample < len(items):
        random.seed(args.seed)
        items = random.sample(items, args.sample)
    if args.limit:
        items = items[:args.limit]

    # Resume: skip UPCs already finished (failed ones are retried).
    done = set()
    if os.path.exists(args.output):
        with open(args.output, encoding="utf-8") as f:
            done = {row["REAL_UPC"] for row in csv.DictReader(f) if row["HTS10_LOCAL"]}
    todo = [it for it in items if it["REAL_UPC"] not in done]
    print(f"{len(items)} products, {len(done)} already done, {len(todo)} to do")
    if todo:
        load_model()
        print(f"Classifying (about 3-11 model calls per product; progress every 5 products)...")

    new_file = not os.path.exists(args.output)
    out = open(args.output, "a", newline="", encoding="utf-8")
    writer = csv.writer(out)
    if new_file:
        writer.writerow(["REAL_UPC", "BRAND", "CATEGORY", "PROD_DESC", "HTS10_LOCAL",
                         "DIGITS", "MODEL_CALLS", "PATH", "MODEL"])
    steps_file = open(args.output + ".steps.jsonl", "a", encoding="utf-8")

    # Products with the same text shown to the model are classified once.
    memo = {}

    def work(item):
        text = item_text_for(item, args.desc_only)
        if text not in memo:
            memo[text] = classify(tree, text)
        return item, memo[text]

    start = time.time()
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, (item, (code, path, steps, calls)) in enumerate(pool.map(work, todo), start=1):
            if not code:
                failed += 1  # not written; retried on the next run
            else:
                writer.writerow([item["REAL_UPC"], item["BRAND"], item["CATEGORY"],
                                 item["PROD_DESC"], code, len(code), calls, " > ".join(path), args.model])
                steps_file.write(json.dumps({"REAL_UPC": item["REAL_UPC"], "steps": steps}) + "\n")
                out.flush()
                steps_file.flush()
            if n % 5 == 0 or n == len(todo):
                rate = (time.time() - start) / n
                print(f"  {n}/{len(todo)} done, {rate:.2f} s per product, {failed} failed")
    out.close()
    steps_file.close()

    if todo:
        rate = (time.time() - start) / len(todo)
        print(f"\n{len(todo)} products in {(time.time() - start) / 60:.1f} min ({rate:.2f} s each), "
              f"{failed} failed (rerun the same command to retry them)")
        print(f"Projected for {args.project:,} products at this rate: {args.project * rate / 3600:.0f} hours "
              f"({args.project * rate / 86400:.1f} days)")


if __name__ == "__main__":
    main()
