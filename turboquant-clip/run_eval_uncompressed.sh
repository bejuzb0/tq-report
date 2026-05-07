#!/bin/bash
set -e

echo "=== Running UNCOMPRESSED Evaluation for Flickr30k ==="

# 1. Main Experiment (Uncompressed only, 32 bits, 1 seed, 1000 max queries)
python -m src.eval.experiment --data-dir data --out-dir results --methods uncompressed --bits 32 --seeds 0 --max-queries 1000

# 2. Geometry analysis
python -m src.analysis.geometry --data-dir data --out-dir results/figures

# 3. Plots
python -m src.analysis.plots --results-dir results --dataset data

echo "=== Running UNCOMPRESSED Evaluation for MSCOCO ==="

# 1. Main Experiment (Uncompressed only, 32 bits, 1 seed, 1000 max queries)
python -m src.eval.experiment --data-dir data_coco --out-dir results --methods uncompressed --bits 32 --seeds 0 --max-queries 1000

# 2. Geometry analysis
python -m src.analysis.geometry --data-dir data_coco --out-dir results/figures

# 3. Plots
python -m src.analysis.plots --results-dir results --dataset data_coco

echo "=== All uncompressed evaluations finished! ==="
