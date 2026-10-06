"""Collect Kubernetes pod status and events for the IA demo."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def kubectl_json(*args):
    command = [
        "kubectl",
        "--context=docker-desktop",
        "--namespace=ia-demo",
        *args,
        "-o",
        "json",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def collect():
    pods = kubectl_json("get", "pods", "-l", "app=rewriter")
    events = kubectl_json("get", "events")

    pod_ids = {
        pod["metadata"]["uid"]
        for pod in pods.get("items", [])
    }

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "context": "docker-desktop",
        "namespace": "ia-demo",
        "pods": [
            {
                "name": pod["metadata"]["name"],
                "status": pod.get("status", {}),
            }
            for pod in pods.get("items", [])
        ],
        "events": [
            {
                "reason": event.get("reason"),
                "message": event.get("message"),
                "type": event.get("type"),
                "count": event.get("count"),
                "last_timestamp": event.get("lastTimestamp"),
                "pod": event.get("involvedObject", {}).get("name"),
            }
            for event in events.get("items", [])
            if event.get("involvedObject", {}).get("uid") in pod_ids
        ],
    }


if __name__ == "__main__":
    try:
        evidence = collect()
        output = Path("reports/k8s-evidence.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence, indent=2),
            encoding="utf-8",
        )
        print(f"Evidence saved to: {output}")
        print(f"Pods collected: {len(evidence['pods'])}")
    except (
        subprocess.SubprocessError,
        OSError,
        ValueError,
    ) as error:
        raise SystemExit(f"Collection failed: {error}")