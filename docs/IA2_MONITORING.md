# Personalized Rewriter: IA2 monitoring

This adds Prometheus and Grafana to the `rewriter-ia2` namespace in the
`docker-desktop` Kubernetes context. Prometheus discovers the application pods
using `app=rewriter` and scrapes each pod's named `http` port independently.
Grafana provisions the data source and the six-panel **Personalized Rewriter - IA2**
dashboard in the **DevOps IA2** folder.

The monitoring installer reports the current application image and replica count.
The application Deployment, writing samples and OpenRouter configuration are not
modified by this installer. A working metrics scrape does not establish that
external AI rewriting works; verify that feature separately through the website.

## Install

The ZIP contains three folders: `k8s`, `scripts` and `docs`. Copy these into
`D:\DevOp\personalized-rewriter-ia2`, merging with the existing folders. The
installer uses Python's standard library and the existing `kubectl` executable.

In VS Code PowerShell:

```powershell
cd "D:\DevOp\personalized-rewriter-ia2"
python .\scripts\install_ia2_monitoring.py
```

Requirements: Docker Desktop Kubernetes running, the `rewriter` Deployment in
`rewriter-ia2`, application pod label `app=rewriter`, container `rewriter`, named
port `http` on 8000, and the existing `standard` StorageClass. The installer stops
if these preconditions or server-side manifest validation fail. It preserves an
existing Grafana Secret on reruns and restarts the monitoring Deployments when
their provisioned configuration changes.

The first image downloads can take several minutes. Successful installation ends
with both Deployments rolled out, their pods ready, and their PVCs Bound. This
checks process readiness; verify scraping and dashboard data in the next steps.

## Open Prometheus

Use a new PowerShell terminal and keep it running:

```powershell
kubectl --context docker-desktop -n rewriter-ia2 port-forward --address=127.0.0.1 service/prometheus 9090:9090
```

Open [Prometheus targets](http://localhost:9090/targets). Allow 20-30 seconds
after installation. The `rewriter` scrape pool should show three healthy targets
when the application is at its expected steady state of three running pods.
The additional `prometheus` scrape pool monitors Prometheus itself.

Useful queries:

```promql
up{job="rewriter"}
```

```promql
sum(up{job="rewriter"})
```

During application rolling updates, old and new pods can temporarily coexist.
Targets follow pod discovery and can briefly differ from the steady-state count.

## Open Grafana

Use another PowerShell terminal and keep it running:

```powershell
kubectl --context docker-desktop -n rewriter-ia2 port-forward --address=127.0.0.1 service/grafana 3000:3000
```

Open [Grafana](http://localhost:3000). Username: `admin`.
Retrieve the generated password in a separate terminal and copy it directly into
the Grafana login form:

```powershell
$ia2GrafanaSecret = kubectl --context docker-desktop -n rewriter-ia2 get secret ia2-grafana-admin -o json | ConvertFrom-Json
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($ia2GrafanaSecret.data.'admin-password'))
```

Navigate to **Dashboards -> DevOps IA2 -> Personalized Rewriter - IA2**.
The dashboard is provisioned from `k8s/monitoring/rewriter-dashboard.json`;
the data source is already configured as `IA2 Prometheus`.

## Generate visible application traffic

In another terminal, run this two-minute request burst. It requests the app's
HTML page and makes no OpenRouter calls:

```powershell
1..120 | ForEach-Object {
    $ia2Response = Invoke-WebRequest -UseBasicParsing -DisableKeepAlive -Uri "http://localhost:8002/" -TimeoutSec 5 -ErrorAction Stop
    "Request $_ : HTTP $($ia2Response.StatusCode)"
    Start-Sleep -Seconds 1
}
```

Allow at least two scrapes for a rate to appear. The dashboard uses a one-minute
window and a ten-minute display range. Health probes and Prometheus scrapes are
excluded from the application request and latency panels. `GET /` contributes
real application requests without consuming AI quota. This burst generates
traffic; record success/failure counts separately for the recovery demonstration.

## Dashboard panels

| Panel | Meaning |
|---|---|
| Reachable app metric targets | Successful `up` targets in the application scrape pool; normally three. |
| Application requests / minute | Rate of application HTTP responses over one minute, multiplied by 60. |
| Average response time | Sum of latency rates divided by sum of request-count rates, in milliseconds. |
| HTTP error rate | Percentage of application responses with 4xx or 5xx status. |
| Application requests / minute by pod | Per-pod request rates, including newly discovered replacement pods. |
| Average response time over time | The weighted mean response time plotted over the selected period. |

The response-time panels measure application HTTP requests. They are not an
isolated measurement of model inference time. The target count measures metrics
reachability, not Kubernetes readiness or OpenRouter availability. `No data yet`
can occur before there are enough scrape samples or requests for a rate.

## AI panels

The dashboard has six extra panels for the AI side: OpenRouter latency p95,
AI rewrites per minute, rewrite and OpenRouter failures by reason, AI tokens
per minute by type, and app response time p95. They stay empty until someone
uses the rewrite feature.

## Alerts

`k8s/monitoring/alert-rules.yml` is loaded by Prometheus (see the Alerts page
in Prometheus). Four warning alerts: fewer than 3 healthy pods for 1 minute,
more than 10 percent 5xx responses for 2 minutes, more than 5 OpenRouter
failures in 10 minutes, and p95 response time above 5 seconds for 5 minutes.
No notifications are sent; alerts are only visible in the Prometheus UI.

## Resources and persistence

Images are pinned to Prometheus `v3.13.5`, Grafana OSS `13.2.4`, and BusyBox
`1.37.0` for preparing storage permissions. Two new 1 GiB PVCs store Prometheus
history and Grafana state. Prometheus retains up to three days of data, bounded
by a 512 MB TSDB limit. Both monitoring Deployments have one replica and use
`Recreate` updates for their single-writer storage.

Prometheus has a namespace-scoped ServiceAccount/Role with `get`, `list` and
`watch` access to pods only. Grafana's admin password is generated locally during
installation and stored in a Kubernetes Secret. It is not written to these files.
Both services are ClusterIP services; the commands above bind their local
forwarded ports to `127.0.0.1`.

The app's request traffic still uses its existing LoadBalancer on port 8002.
Use that address when demonstrating traffic during application pod deletion.

## Diagnose an installation problem

```powershell
kubectl --context docker-desktop -n rewriter-ia2 get pods,pvc
kubectl --context docker-desktop -n rewriter-ia2 get events --sort-by=.metadata.creationTimestamp
kubectl --context docker-desktop -n rewriter-ia2 logs deployment/prometheus -c validate-config
kubectl --context docker-desktop -n rewriter-ia2 logs deployment/prometheus -c prometheus --tail=60
kubectl --context docker-desktop -n rewriter-ia2 logs deployment/grafana -c grafana --tail=60
```

If a port-forward stops, run that port-forward command again. If a local port is
already in use, change the left-hand port, for example `3001:3000`, and open
`http://localhost:3001`.

## Commit the monitoring source

After inspecting the new files and verifying the stack:

```powershell
git add k8s/monitoring scripts/install_ia2_monitoring.py docs/IA2_MONITORING.md
git commit -m "Add Prometheus and Grafana monitoring for IA2"
git push origin main
```

The earlier local `k8s/rewriter-ia2.yaml` application manifest can be reviewed and
committed separately. This bundle does not provide automated application
deployment from GitHub Actions to the laptop.

## Evidence to collect after running on your laptop

- Both monitoring pods Running and both monitoring PVCs Bound.
- Prometheus targets with three healthy application targets at steady state.
- Grafana dashboard showing measured traffic and response times.
- Separate application rewrite result, handled missing-sample error, and a
  continuous-traffic pod-replacement demonstration.

Record actual results, timestamps and observed errors. These source files are a
prepared implementation, not evidence that the stack has run on your machine.

## Checks before delivery

The prepared ConfigMaps, Secret shape and stack resources passed strict
Kubernetes 1.36 schema validation (13 objects). Storage, selectors, mounts,
configuration checksums and Secret references were checked for consistency.
All six dashboard queries parsed as valid PromQL. The installer compiled and
rendered its manifests offline. Prometheus also validates its own configuration
with `promtool` in an init container when the stack starts on the laptop.

Local Kubernetes deployment, Prometheus scraping and Grafana dashboard loading
remain runtime checks for the installation procedure above.

## Primary references

- [Prometheus Kubernetes discovery](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#kubernetes_sd_config)
- [Prometheus configuration validation](https://prometheus.io/docs/prometheus/latest/command-line/promtool/#promtool-check-config)
- [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Grafana on Kubernetes](https://grafana.com/docs/grafana/latest/setup-grafana/installation/kubernetes/)
