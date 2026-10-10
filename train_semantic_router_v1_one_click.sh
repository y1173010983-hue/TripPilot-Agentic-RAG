#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/root/autodl-tmp/trippilot-finetune
cd "$ROOT"
source .venv/bin/activate

test -d models/Qwen3-4B-Instruct-2507
python -m pip check
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader

python scripts/generate_semantic_router_v1.py --data-dir "$ROOT/data"
python -m json.tool data/semantic_router_v1/trippilot_semantic_router_v1_train.json >/dev/null
python -m json.tool data/semantic_router_v1/trippilot_semantic_router_v1_dev.json >/dev/null
python -m json.tool data/semantic_router_v1/trippilot_semantic_router_v1_test.json >/dev/null
python -m json.tool data/dataset_info.json >/dev/null

mkdir -p logs
llamafactory-cli train configs/train_qwen3_4b_semantic_router_v1.yaml \
  2>&1 | tee logs/train_qwen3_4b_semantic_router_v1.log

python scripts/run_semantic_router_v1_acceptance.py \
  --model "$ROOT/models/Qwen3-4B-Instruct-2507" \
  --adapter "$ROOT/outputs/qwen3-4b-semantic-router-v1" \
  --dataset "$ROOT/data/semantic_router_v1/trippilot_semantic_router_v1_test.json" \
  --report "$ROOT/outputs/qwen3-4b-semantic-router-v1/semantic_acceptance.json" \
  2>&1 | tee logs/acceptance_qwen3_4b_semantic_router_v1.log

echo "Semantic Router V1 training and acceptance completed."
