#!/usr/bin/env bash
# TripPilot Protocol-V1 一键微调脚本
# 在云服务器 /root/autodl-tmp/trippilot-finetune 下执行：
#   bash train_protocol_v1_one_click.sh
set -Eeuo pipefail

ROOT=/root/autodl-tmp/trippilot-finetune
cd "$ROOT"
source .venv/bin/activate

test -d "$ROOT/models/Qwen3-4B-Instruct-2507"
python -m pip check
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader

GEN=generate_preference_protocol_v1.py
if [ -f "$ROOT/scripts/$GEN" ]; then
  GEN="$ROOT/scripts/$GEN"
elif [ -f "$ROOT/$GEN" ]; then
  GEN="$ROOT/$GEN"
else
  echo "找不到 $GEN，请把生成器放到 scripts/ 或项目根目录" >&2
  exit 1
fi

CFG=train_qwen3_4b_qlora_protocol_v1.yaml
if [ -f "$ROOT/scripts/$CFG" ]; then
  mkdir -p "$ROOT/configs"
  cp "$ROOT/scripts/$CFG" "$ROOT/configs/$CFG"
elif [ -f "$ROOT/$CFG" ]; then
  mkdir -p "$ROOT/configs"
  cp "$ROOT/$CFG" "$ROOT/configs/$CFG"
elif [ ! -f "$ROOT/configs/$CFG" ]; then
  echo "找不到 $CFG，也无法在 configs/ 中找到，请先上传训练配置" >&2
  exit 1
fi

python "$GEN" \
  --output "$ROOT/data/formal/train/trippilot_preference_protocol_v1.json" \
  --dataset-info "$ROOT/data/dataset_info.json" \
  --dataset-name trippilot_preference_protocol_v1

python -m json.tool "$ROOT/data/formal/train/trippilot_preference_protocol_v1.json" >/dev/null
python -m json.tool "$ROOT/data/dataset_info.json" >/dev/null

mkdir -p "$ROOT/logs"
llamafactory-cli train "$ROOT/configs/train_qwen3_4b_qlora_protocol_v1.yaml" \
  2>&1 | tee "$ROOT/logs/train_qwen3_4b_qlora_protocol_v1.log"

ACCEPT=run_protocol_v1_acceptance.py
if [ -f "$ROOT/scripts/$ACCEPT" ]; then
  ACCEPT="$ROOT/scripts/$ACCEPT"
elif [ -f "$ROOT/$ACCEPT" ]; then
  ACCEPT="$ROOT/$ACCEPT"
else
  echo "找不到 $ACCEPT，请把验收脚本放到 scripts/ 或项目根目录" >&2
  exit 1
fi

python "$ACCEPT" \
  --model "$ROOT/models/Qwen3-4B-Instruct-2507" \
  --adapter "$ROOT/outputs/qwen3-4b-preference-protocol-v1" \
  --report "$ROOT/outputs/qwen3-4b-preference-protocol-v1/protocol_v1_acceptance_30.json" \
  2>&1 | tee "$ROOT/logs/acceptance_protocol_v1.log"

echo ""
echo "Protocol-V1 训练与验收完成："
echo "  Adapter: $ROOT/outputs/qwen3-4b-preference-protocol-v1"
echo "  报告:    $ROOT/outputs/qwen3-4b-preference-protocol-v1/protocol_v1_acceptance_30.json"
