#!/bin/zsh
# After both tests have run with the same N: one review CSV with both models side by side.
#
#   ./compare_models.sh [N]

set -e -o pipefail
cd "$(dirname "$0")"
N=${1:-100}
python3 make_review.py results/gptoss120b_n$N.csv results/qwen36_n$N.csv \
    --n $N --out results/review_both_n$N.csv
echo
echo "Speed:"
grep -H "Projected" results/gptoss120b_n$N.log results/qwen36_n$N.log
