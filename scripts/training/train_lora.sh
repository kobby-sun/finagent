#!/usr/bin/env bash
# LoRA fine-tune a small instruct model on Apple Silicon with mlx-lm (optional dependency).
#   pip install 'finagent[training]'      # mlx-lm
#   scripts/training/train_lora.sh [base_model] [iters]
set -euo pipefail
cd "$(dirname "$0")/../.."
BASE="${1:-Qwen/Qwen2.5-1.5B-Instruct}"
ITERS="${2:-600}"
python scripts/training/generate_dataset.py
python -m mlx_lm.lora --model "$BASE" --train --data training/data --iters "$ITERS" \
  --batch-size 2 --num-layers 16 --learning-rate 1e-4 --max-seq-length 8192 --adapter-path training/adapters
python -m mlx_lm.fuse --model "$BASE" --adapter-path training/adapters --save-path training/fused
echo "Fused model in training/fused. Convert to GGUF with llama.cpp (see docs/local_llm_training.md), then build_ollama_model.sh"
