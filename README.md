# upc-hts-classifier

Classify retail products (UPC, brand, category, description) into U.S. HTS-10 codes
with a local LLM, following the hierarchical method of Cavallo et al., Appendix B.2.1.
Everything runs on your own Mac through [Ollama](https://ollama.com); nothing is sent
to an outside service.

This repository tests two models on the same products and writes a clean CSV for review:

| Test | Model | Size | Expected speed |
|---|---|---|---|
| `run_test_gptoss.sh` | gpt-oss 120B (reasoning "low") | 65 GB | slower, likely more accurate |
| `run_test_qwen.sh` | Qwen3.6 35B-A3B, 8-bit (reasoning off) | 39 GB | about 5–10× faster |

## Method

The model walks down the HTS tree one level at a time:
section (21) → chapter → heading (4-digit) → subheading (6) → … → statistical line (10).
At each step it sees the item and the immediate subcategories of the node it chose
last, and must return exactly one code from that list:

    The item has already been classified as: <path so far>

    Classify the following item into one of these subcategories:
    08: Edible fruit and nuts; peel of citrus fruit or melons [Note: ...]
    ...
    Item: "BANANAS (brand: DOLE; retail category: FRUIT)"
    Return exactly one code from the list.

- Replies must be one of the listed codes (enforced with a JSON schema; any other
  reply is rejected and asked again).
- A node with a single child is passed through without asking.
- Unnumbered grouping rows in the HTS (e.g. "Horses:") are offered as G1, G2, ….
- No labeled data is used. Claude's 100 codes are used only to check results.
- About 6–7 model calls per product on average (3 to 11).

Clarifying notes ("enriched labels" in the paper) are in `hts_notes.csv`; they are
appended to section, chapter and heading labels as `[Note: ...]`. They are our own
short retail examples, since the paper's are not published; edit them freely.
Key = `section IV`, `chapter 19`, or an HTS number as written (`1905`, `2202.99`).

Two choices that go slightly beyond the paper's text:
1. The path chosen so far is shown, because many HTS labels are just "Other".
2. The item text includes brand and retail category. Add `--desc-only` to
   `hts_cascade.py` to show the description alone.

## Files

| File | What it is |
|---|---|
| `hts_cascade.py` | the classifier |
| `make_review.py` | builds the review CSV and prints agreement tables |
| `run_test_gptoss.sh`, `run_test_qwen.sh` | one test per model |
| `compare_models.sh` | both models side by side, after both tests have run |
| `hts_notes.csv` | clarifying notes added to HTS labels |
| `data/htsdata.csv` | the HTS schedule (public, from hts.usitc.gov) |
| `data/claude_hts10_first100.csv` | **not in git** (product data); put your copy here |

## Setup (once)

1. A Mac with Apple silicon. 128 GB of memory for gpt-oss 120B; 64 GB is enough for Qwen alone.
2. Install Ollama from https://ollama.com/download. Python 3.9 or newer (the macOS
   `python3` is fine); no extra packages are needed.
3. Clone this repository and copy the product files into `data/`:
   ```
   git clone https://github.com/oruc47/upc-hts-classifier.git
   cd upc-hts-classifier
   cp /path/to/claude_hts10_first100.csv data/
   ```
   An input CSV needs the columns `REAL_UPC`, `BRAND`, `CATEGORY`, `PROD_DESC`
   (`COMPANY` is optional and ignored).

## Running the tests

**1. Start Ollama.** Quit the Ollama menu-bar app if it is running, then in a
terminal window that you leave open:
```
OLLAMA_NUM_PARALLEL=8 OLLAMA_FLASH_ATTENTION=1 ollama serve
```

**2. In a second terminal, run either test or both** (each downloads its model the first time):
```
./run_test_qwen.sh                     # Claude's 100 products
./run_test_gptoss.sh
```
Both take two optional arguments, the input file and the number of products N:
```
./run_test_qwen.sh data/all_items.csv 500      # random 500 products from a bigger file
./run_test_gptoss.sh data/all_items.csv 500
```
The N products are a random sample (seed 1), so both tests see the same products
when given the same file and N. With Claude's file and N = 100, both use all of it.
A 100-product test takes minutes for Qwen and longer for gpt-oss; each script
prints seconds per product and a projected time for 300,000 products.
If a test stops, run the same command again; it continues where it left off.

**3. Compare** (after both tests ran with the same N):
```
./compare_models.sh 100
```

## Outputs (in `results/`)

| File | Contents |
|---|---|
| `review_qwen36_n100.csv`, `review_gptoss120b_n100.csv` | review file for one model |
| `review_both_n100.csv` | both models side by side |
| `qwen36_n100.csv`, `gptoss120b_n100.csv` | raw output: code, digits, number of model calls, full path |
| `*.steps.jsonl` | every step: options shown and option chosen (for the ambiguity analysis in Appendix B.2.2) |
| `*.log` | progress and speed |

Review file columns, one row per product (sorted by category):
- `REAL_UPC, BRAND, CATEGORY, PROD_DESC`
- per model: `_HTS10` (e.g. `1905.90.10.85`), `_DESCRIPTION` (official text from the
  heading down to the 10-digit line), `_GENERAL_DUTY` (general rate of duty of the
  line, or of the nearest line above it that has one)
- if Claude's code exists: `CLAUDE_HTS10`, `CLAUDE_DESCRIPTION`, and per model
  `_DIGITS_AGREE_WITH_CLAUDE` (10, 8, 6, 4, 2 or 0 leading digits in common)
- with two models: `DIGITS_MODELS_AGREE`
- empty `REVIEWER_CORRECT_HTS10` and `REVIEWER_NOTES` for your review

The scripts also print a table of agreement at 2/4/6/8/10 digits.

## Choosing a model

Look mainly at agreement at 6 and 8 digits (most tariffs are set at 8 digits;
Appendix B.2.2, Table A3) and at the products where the two models disagree.
Claude's codes are a reference, not ground truth. The last two digits often turn on
facts a retail description does not contain (quota status, packaging, fat content).

## Full run

```
caffeinate -i python3 hts_cascade.py --model qwen3.6:35b-a3b-q8_0 --think off --workers 8 \
    --input data/all_items.csv --output results/all_qwen36.csv
```
or `--model gpt-oss:120b --think low --workers 4`. `caffeinate -i` keeps the Mac
awake; the run can be stopped and restarted. Then, for example:
```
python3 make_review.py results/all_qwen36.csv --n 1000 --out results/review_all_qwen36.csv
```
Rough time for 300,000 products on a 128 GB Mac: Qwen 3–6 days, gpt-oss 2–4 weeks
(products with identical text are classified once). On a cluster GPU with vLLM it
would be hours rather than days; that needs a small change to the code.
