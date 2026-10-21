"""Install the IA2 monitoring stack with Python's standard library and kubectl."""

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "k8s" / "monitoring"
CONTEXT = "docker-desktop"
NAMESPACE = "rewriter-ia2"
KUBECTL = ["kubectl", "--context", CONTEXT, "--namespace", NAMESPACE]
LABELS = {"app.kubernetes.io/part-of": "rewriter-ia2-monitoring"}
SECRET_NAME = "ia2-grafana-admin"


def run(*args, body=None, quiet=False, timeout=90):
    """Use checked argument lists; never place credentials in command arguments."""
    result = subprocess.run(
        [*KUBECTL, *args], input=body, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode:
        message = result.stderr.strip() or "kubectl returned an error."
        raise RuntimeError(f"kubectl {' '.join(args)}: {message}")
    if not quiet:
        if result.stdout.strip():
            print(result.stdout.strip(), flush=True)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr, flush=True)
    return result.stdout


def configmap(name, data):
    return {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": LABELS},
        "data": data,
    }


def render_resources():
    """Render deterministic ConfigMaps and Deployment checksums; contains no secrets."""
    prom = {name: (CONFIG / name).read_text(encoding="utf-8") for name in ("prometheus.yml", "alert-rules.yml")}
    provisioning = {
        "datasource.yml": (CONFIG / "grafana-datasource.yml").read_text(encoding="utf-8"),
        "dashboards.yml": (CONFIG / "grafana-dashboard-provider.yml").read_text(encoding="utf-8"),
    }
    dashboard = {"rewriter-dashboard.json": (CONFIG / "rewriter-dashboard.json").read_text(encoding="utf-8")}
    json.loads(dashboard["rewriter-dashboard.json"])
    maps = {"apiVersion": "v1", "kind": "List", "items": [
        configmap("ia2-prometheus-config", prom),
        configmap("ia2-grafana-provisioning", provisioning),
        configmap("ia2-grafana-dashboard", dashboard),
    ]}
    stack = (CONFIG / "stack.yaml").read_text(encoding="utf-8")
    replacements = {
        "__PROMETHEUS_CONFIG_SHA256__": prom,
        "__GRAFANA_CONFIG_SHA256__": {**provisioning, **dashboard},
    }
    for token, content in replacements.items():
        if stack.count(token) != 1:
            raise RuntimeError(f"Expected exactly one {token} in stack.yaml.")
        checksum = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        stack = stack.replace(token, checksum)
    return json.dumps(maps), stack


def ensure_grafana_secret():
    existing = run("get", "secret", SECRET_NAME, "--ignore-not-found", "-o", "json", quiet=True)
    if existing.strip():
        keys = json.loads(existing).get("data", {})
        if not all(keys.get(key) for key in ("admin-user", "admin-password")):
            raise RuntimeError(f"Existing {SECRET_NAME} must contain admin-user and admin-password.")
        print("Keeping the existing Grafana login.", flush=True)
        return
    secret = {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE, "labels": LABELS},
        "stringData": {"admin-user": "admin", "admin-password": secrets.token_urlsafe(24)},
    }
    # create avoids storing a plaintext password in a last-applied annotation.
    run("create", "-f", "-", body=json.dumps(secret))


def install():
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl was not found. Run this in your VS Code PowerShell terminal.")
    print(f"Using context {CONTEXT}, namespace {NAMESPACE}.", flush=True)
    deployment = json.loads(run("get", "deployment", "rewriter", "-o", "json", quiet=True))
    spec = deployment["spec"]["template"]["spec"]
    app = next((c for c in spec["containers"] if c["name"] == "rewriter"), None)
    labels = deployment["spec"]["template"]["metadata"].get("labels", {})
    if not app or labels.get("app") != "rewriter":
        raise RuntimeError("The application must have container rewriter and pod label app=rewriter.")
    if not any(port.get("name") == "http" and port.get("containerPort") == 8000 for port in app.get("ports", [])):
        raise RuntimeError("The application's port 8000 must be named http for this scrape configuration.")
    ready = deployment.get("status", {}).get("readyReplicas", 0)
    print(f"Application ready replicas: {ready}/{deployment['spec'].get('replicas', 1)}", flush=True)
    print(f"Application image: {app['image']}", flush=True)
    run("get", "storageclass", "standard", "-o", "name", quiet=True)
    maps, stack = render_resources()
    print("Checking manifests against your Kubernetes API...", flush=True)
    run("apply", "--dry-run=server", "-f", "-", body=maps, quiet=True)
    run("apply", "--dry-run=server", "-f", "-", body=stack, quiet=True)
    ensure_grafana_secret()
    run("apply", "-f", "-", body=maps)
    run("apply", "-f", "-", body=stack)
    for name in ("prometheus", "grafana"):
        print(f"Waiting for {name}; the first image download can take a few minutes...", flush=True)
        run("rollout", "status", f"deployment/{name}", "--timeout=300s", timeout=330)
    run("get", "pods,pvc,svc", "-l", "app.kubernetes.io/part-of=rewriter-ia2-monitoring")
    print("\nMonitoring deployments are ready. Next, verify Prometheus targets and open Grafana.", flush=True)
    print("Instructions: docs/IA2_MONITORING.md", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", metavar="DIRECTORY", help="Render manifests offline without contacting Kubernetes.")
    args = parser.parse_args()
    try:
        if args.render:
            folder = Path(args.render)
            folder.mkdir(parents=True, exist_ok=True)
            maps, stack = render_resources()
            (folder / "configmaps.json").write_text(maps + "\n", encoding="utf-8")
            (folder / "stack.yaml").write_text(stack, encoding="utf-8")
            print(f"Rendered manifests to {folder.resolve()}")
        else:
            install()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"Monitoring setup stopped: {error}", file=sys.stderr)
        print("For pod diagnostics, see docs/IA2_MONITORING.md.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSetup interrupted. Run the same command again to continue.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
