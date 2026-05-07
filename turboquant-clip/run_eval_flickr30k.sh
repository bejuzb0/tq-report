#!/bin/bash
set -e

echo "Starting evaluation for Flickr30k..."

# 1. Run main experiment suite (Recall and memory profiling)
# Note: You can customize --methods, --bits, and --seeds if you don't want to run the full sweep
python -m src.eval.experiment --data-dir data --out-dir results

# 2. Run analysis scripts
python -m src.analysis.geometry --data-dir data --out-dir results/figures
python -m src.analysis.failure_modes --data-dir data --out-dir results
python -m src.analysis.plots --results-dir results --dataset data

echo "Flickr30k evaluation complete! Output files are prefixed with 'data_'."
