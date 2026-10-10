#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/root/autodl-tmp/trippilot-finetune
cd "$ROOT"
source .venv/bin/activate

test -d models/Qwen3-4B-Instruct-2507
test -f outputs/qwen3-4b-preference-final-v4/adapter_config.json
python -m pip check
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader

python scripts/generate_preference_v5_final.py \
  --output "$ROOT/data/formal/train/trippilot_preference_final_v5.json" \
  --dataset-info "$ROOT/data/dataset_info.json" \
  --dataset-name trippilot_preference_final_v5

python -m json.tool data/formal/train/trippilot_preference_final_v5.json >/dev/null
python -m json.tool data/dataset_info.json >/dev/null

mkdir -p logs
llamafactory-cli train configs/train_qwen3_4b_qlora_final_v5.yaml \
  2>&1 | tee logs/train_qwen3_4b_qlora_final_v5.log

python scripts/run_v4_realistic_acceptance.py \
  --model "$ROOT/models/Qwen3-4B-Instruct-2507" \
  --adapter "$ROOT/outputs/qwen3-4b-preference-final-v5" \
  --report "$ROOT/outputs/qwen3-4b-preference-final-v5/realistic_acceptance_30.json" \
  2>&1 | tee logs/acceptance_qwen3_4b_final_v5.log

echo "V5 训练与 30 条验收全部完成。"
