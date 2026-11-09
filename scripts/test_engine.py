from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine import engine_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Ověří skutečný chatový run Lindy přes Fresh AI Engine.")
    parser.add_argument("--project-id", default="1", help="ID projektu použité pro diagnostický run (výchozí 1)")
    args = parser.parse_args()

    health = engine_client.health()
    print("ENGINE HEALTH")
    print(json.dumps(health, ensure_ascii=False, indent=2, default=str))
    if not health.get("ok"):
        return 2

    routes = [
        ("chat", "chat.conversation"),
        ("general", "chat.conversation"),
    ]
    attempts = []
    for task_type, operation in routes:
        result = engine_client.run(
            project_id=args.project_id,
            task_type=task_type,
            operation=operation,
            quality="economy",
            prompt=(
                "Jsi Linda, AI marketingová kolegyně. Toto je diagnostický test spojení. "
                "Odpověz přesně jednou krátkou českou větou, že spojení Lindy s Fresh AI Engine funguje."
            ),
            context={"diagnostic": True, "agent": "Linda"},
            input_data={"diagnostic": True},
            output_contract=None,
            max_cost_usd=0.03,
        )
        audit = result.audit()
        audit["task_type"] = task_type
        audit["operation"] = operation
        attempts.append(audit)
        print(f"\nRUN {task_type} / {operation}")
        print(json.dumps({"audit": audit, "result": result.result}, ensure_ascii=False, indent=2, default=str))
        if result.ok and result.result:
            print("\nENGINE RUN OK")
            return 0
        if result.http_status in {401, 403, 429}:
            break

    print("\nENGINE RUN FAILED")
    print(json.dumps({"attempts": attempts}, ensure_ascii=False, indent=2, default=str))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
