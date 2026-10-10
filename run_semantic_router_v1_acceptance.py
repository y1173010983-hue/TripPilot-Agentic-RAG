"""Evaluate the semantic-router adapter with field-level and strict metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from llamafactory.chat import ChatModel


TOP_KEYS = {"operation", "domains", "entities", "state_changes", "clarification"}


def normalized_changes(items):
    return sorted(
        (item.get("field"), item.get("operation"), json.dumps(item.get("value"), ensure_ascii=False, sort_keys=True))
        for item in items
    )


def score(actual: dict, expected: dict) -> dict:
    protocol = set(actual) == TOP_KEYS and isinstance(actual.get("domains"), list) and isinstance(actual.get("entities"), dict) and isinstance(actual.get("state_changes"), list)
    operation = actual.get("operation") == expected["operation"]
    domains = set(actual.get("domains", [])) == set(expected["domains"])
    expected_entities = expected["entities"]
    actual_entities = actual.get("entities", {})
    entities = all(actual_entities.get(key) == value for key, value in expected_entities.items())
    changes = normalized_changes(actual.get("state_changes", [])) == normalized_changes(expected["state_changes"])
    clarification = bool(actual.get("clarification")) == bool(expected.get("clarification"))
    strict = protocol and operation and domains and entities and changes and clarification
    return {"protocol": protocol, "operation": operation, "domains": domains, "entities": entities, "state_changes": changes, "clarification": clarification, "strict": strict}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    chat = ChatModel({"model_name_or_path": args.model, "adapter_name_or_path": args.adapter, "template": "qwen3_nothink", "infer_backend": "huggingface", "trust_remote_code": True})
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    results = []
    totals = {key: 0 for key in ("protocol", "operation", "domains", "entities", "state_changes", "clarification", "strict")}
    for index, item in enumerate(dataset, 1):
        system, user, assistant = item["messages"]
        expected = json.loads(assistant["content"])
        raw = chat.chat([{"role": "user", "content": user["content"]}], system=system["content"], do_sample=False, max_new_tokens=512)[0].response_text
        try:
            actual = json.loads(raw)
            metrics = score(actual, expected)
        except (json.JSONDecodeError, TypeError, ValueError):
            actual = None
            metrics = {key: False for key in totals}
        for key, passed in metrics.items():
            totals[key] += int(passed)
        results.append({"index": index, "passed": metrics["strict"], "metrics": metrics, "expected": expected, "actual": actual, "raw": raw})
        print(f"{index:03d}/{len(dataset)} {'PASS' if metrics['strict'] else 'FAIL'} operation={expected['operation']}")

    summary = {key: {"passed": value, "total": len(dataset), "rate": round(value / len(dataset), 4)} for key, value in totals.items()}
    report = {"summary": summary, "results": results}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report={args.report}")


if __name__ == "__main__":
    main()
