#!/bin/bash
set -e

echo "Starting evaluation for MSCOCO..."

# 1. Run main experiment suite (Recall and memory profiling)
# Note: You can customize --methods, --bits, and --seeds if you don't want to run the full sweep
python -m src.eval.experiment --data-dir data_coco --out-dir results

# 2. Run analysis scripts
python -m src.analysis.geometry --data-dir data_coco --out-dir results/figures
python -m src.analysis.failure_modes --data-dir data_coco --out-dir results
python -m src.analysis.plots --results-dir results --dataset data_coco

echo "MSCOCO evaluation complete! Output files are prefixed with 'data_coco_'."
