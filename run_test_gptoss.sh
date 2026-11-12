#!/bin/zsh
# Test gpt-oss:120b on N products and write a review CSV.
#
#   ./run_test_gptoss.sh [INPUT_CSV] [N]
#
# INPUT_CSV defaults to data/claude_hts10_first100.csv (so results can be compared
# with Claude's codes); N defaults to 100. Products are a random sample of N rows
# (seed 1), so the Qwen test with the same INPUT_CSV and N sees the same products.
#
# Ollama must be running first (see README):
#   OLLAMA_NUM_PARALLEL=8 OLLAMA_FLASH_ATTENTION=1 ollama serve

set -e -o pipefail
cd "$(dirname "$0")"
INPUT=${1:-data/claude_hts10_first100.csv}
N=${2:-100}
MODEL=gpt-oss:120b
NAME=gptoss120b_n$N

curl -s http://127.0.0.1:11434/api/version > /dev/null || {
  echo "Ollama is not running. In another terminal run:"
  echo "  OLLAMA_NUM_PARALLEL=8 OLLAMA_FLASH_ATTENTION=1 ollama serve"
  exit 1
}
mkdir -p results
ollama pull $MODEL

python3 hts_cascade.py --model $MODEL --think low --workers 4 \
    --input "$INPUT" --sample $N --seed 1 --project 300000 \
    --output results/$NAME.csv | tee results/$NAME.log

python3 make_review.py results/$NAME.csv --n $N --out results/review_$NAME.csv
