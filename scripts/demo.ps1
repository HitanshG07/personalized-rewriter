<#
LT-I live demo helper. Every scenario runs on its own demo/<scenario> branch + PR that is NEVER merged,
so main always stays green.

  .\scripts\demo.ps1 check                  preflight: tools, Docker, GitHub login, clean tree, main green, AI quota
  .\scripts\demo.ps1 tests                  run the test suite locally (OpenRouter mocked, 0 AI calls)
  .\scripts\demo.ps1 break  <scenario>      push a failing change + open a PR  -> CI red -> AI diagnosis on the PR
  .\scripts\demo.ps1 status <scenario>      job results of the latest run for that PR
  .\scripts\demo.ps1 fix    <scenario>      push the verified fix to the same PR -> CI green, ai-diagnose skipped
  .\scripts\demo.ps1 close  <scenario>      close the PR (not merged) and delete the demo branch
  .\scripts\demo.ps1 compare                Docker before/after table from the saved benchmarks
  .\scripts\demo.ps1 before [-NoCache]      LIVE: build the ORIGINAL image (Dockerfile.baseline), show size, user, CRITICAL CVEs
  .\scripts\demo.ps1 after  [-NoCache]      LIVE: build the AI-OPTIMIZED image (Dockerfile), show size, user, CRITICAL CVEs

Kubernetes + monitoring (IA-II, Docker Desktop Kubernetes, namespace rewriter-ia2):
  .\scripts\demo.ps1 deploy                 deploy the latest scanned main image (hitansh07) with 3 replicas + install monitoring
  .\scripts\demo.ps1 open                   open the app at http://localhost:8002
  .\scripts\demo.ps1 monitor                open Prometheus (localhost:9090) and Grafana (localhost:3000) in new windows
  .\scripts\demo.ps1 traffic [-Requests N] [-Rewrites N]   send demo traffic (Rewrites use real AI calls)
  .\scripts\demo.ps1 heal                   self-healing demo: delete one Pod and watch Kubernetes replace it

Scenarios:
  ci-test   validation bug (empty notes accepted)          -> fails at: test
  security  backup helper using subprocess shell=True      -> fails at: source-security (Bandit)
  trivy     original baseline Dockerfile (pinned digest)   -> fails at: build-scan (Trivy)

Each 'break' costs 1 free AI call (the diagnosis). 'fix', 'check', 'tests' and 'compare' cost none.
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("check", "tests", "break", "status", "fix", "close", "compare", "before", "after",
                 "deploy", "open", "monitor", "traffic", "heal")]
    [string]$Command,
    [Parameter(Position = 1)]
    [ValidateSet("ci-test", "security", "trivy")]
    [string]$Scenario,
    [switch]$NoCache,
    [int]$Requests = 60,
    [int]$Rewrites = 0
)
$ErrorActionPreference = "Continue"  # native tools write progress to stderr; failures are checked explicitly
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Utf8 = New-Object System.Text.UTF8Encoding $false
$Repo = "HitanshG07/personalized-rewriter"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

# Measured baseline image (docs/evidence/docker_baseline.json), pinned by digest so the red run is reproducible.
$BaselineDockerfile = @'
FROM python:3.11@sha256:70e8e937c1da72df1688e883a2108b0feff03381187fa78eca6d9b10df411320

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
'@

$Scenarios = @{
    "ci-test"  = @{ Title = "Relax rewrite request validation for short inputs"; FailsAt = "test" }
    "security" = @{ Title = "Add database backup helper for maintenance"; FailsAt = "source-security (Bandit)" }
    "trivy"    = @{ Title = "Revert to the original Dockerfile"; FailsAt = "build-scan (Trivy)" }
}

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }
function GitOk { & git.exe @args; if ($LASTEXITCODE -ne 0) { throw "git $args failed" } }
function Read-Text($path) { [IO.File]::ReadAllText((Join-Path $Root $path)).Replace("`r`n", "`n") }
function Write-Text($path, $text) { [IO.File]::WriteAllText((Join-Path $Root $path), $text.Replace("`r`n", "`n"), $Utf8) }
function Run-Python($code) { $code | & $Python - }

function Edit-File($path, $old, $new) {
    $text = Read-Text $path
    if (-not $text.Contains($old)) { throw "Expected text not found in $path (is this scenario already applied?)" }
    Write-Text $path ($text.Replace($old, $new))
}

function Assert-Clean {
    $dirty = git status --porcelain --untracked-files=no
    if ($dirty) { throw "Uncommitted changes in tracked files. Commit or stash them first:`n$dirty" }
}

function Assert-Scenario { if (-not $Scenario) { throw "Name a scenario: ci-test, security or trivy" } }
function Branch { "demo/$Scenario" }
function Open-Pr { (gh pr list --head (Branch) --state open --json number | ConvertFrom-Json | Select-Object -First 1).number }

function Apply-Break {
    switch ($Scenario) {
        "ci-test" {
            Edit-File "app/schemas.py" "Field(min_length=1, max_length=2000)" "Field(min_length=0, max_length=2000)"
        }
        "security" {
            Edit-File "app/storage.py" "import sqlite3`n" "import sqlite3`nimport subprocess`n"
            $helper = @'


def backup_db() -> None:
    """Quick local copy of the database before maintenance."""
    path = _db_path()
    subprocess.run(f"cp {path} {path}.bak", shell=True, check=True)
'@
            Write-Text "app/storage.py" ((Read-Text "app/storage.py").TrimEnd() + $helper + "`n")
        }
        "trivy" { Write-Text "Dockerfile" $BaselineDockerfile }
    }
}

function Apply-Fix {
    switch ($Scenario) {
        "ci-test" {
            Edit-File "app/schemas.py" "Field(min_length=0, max_length=2000)" "Field(min_length=1, max_length=2000)"
            return "Fix: reject empty/whitespace notes again (NotesText min_length=1)"
        }
        "security" {
            Edit-File "app/storage.py" "import sqlite3`nimport subprocess`n" "import shutil`nimport sqlite3`n"
            Edit-File "app/storage.py" 'subprocess.run(f"cp {path} {path}.bak", shell=True, check=True)' 'shutil.copy2(path, f"{path}.bak")  # no shell, no command injection'
            return "Fix: back up the database with shutil.copy2 instead of a shell command"
        }
        "trivy" {
            GitOk checkout -q origin/main -- Dockerfile
            return "Fix: apply the AI-recommended, developer-verified Dockerfile (slim base, non-root, multi-stage)"
        }
    }
}

$Image = "hitansh07/personalized-rewriter"
$Ctx = "docker-desktop"
$Ns = "rewriter-ia2"
$AppUrl = "http://127.0.0.1:8002"

function Kube { & kubectl --context $Ctx -n $Ns @args }

function Assert-Cluster {
    kubectl --context $Ctx get nodes 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop Kubernetes is not running (Docker Desktop -> Settings -> Kubernetes -> Enable)" }
}

function Start-Forward($svc, $ports, $title) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "`$host.UI.RawUI.WindowTitle='$title (keep open)'; kubectl --context $Ctx -n $Ns port-forward --address=127.0.0.1 service/$svc $ports"
}

function Latest-Run { (gh run list --branch (Branch) --limit 1 --json databaseId | ConvertFrom-Json)[0].databaseId }

function Show-Status {
    $pr = Open-Pr
    $run = Latest-Run
    if ($pr) { Say "PR   : https://github.com/$Repo/pull/$pr" }
    Say "Run  : https://github.com/$Repo/actions/runs/$run"
    $info = gh run view $run --json status,conclusion,jobs | ConvertFrom-Json
    $overall = if ($info.conclusion) { $info.conclusion } else { $info.status }
    Say "Result: $overall" $(if ($overall -eq "success") { "Green" } elseif ($overall -eq "failure") { "Red" } else { "Yellow" })
    foreach ($j in $info.jobs) {
        $c = if ($j.conclusion) { $j.conclusion } else { $j.status }
        $color = switch ($c) { "success" { "Green" } "failure" { "Red" } default { "DarkGray" } }
        Write-Host ("  {0,-16} {1}" -f $j.name, $c) -ForegroundColor $color
    }
}

function Wait-Run {
    Start-Sleep -Seconds 10
    $run = Latest-Run
    Say "Watching https://github.com/$Repo/actions/runs/$run (about 1-1.5 min)..." "Yellow"
    gh run watch $run --interval 5 2>$null | Out-Null
    Show-Status
}

try {
    switch ($Command) {
        "check" {
            foreach ($tool in "git", "gh", "docker", "trivy", "gitleaks") {
                if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found on PATH" }
            }
            docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running" }
            gh auth status 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Not logged in to GitHub. Run: gh auth login" }
            Assert-Clean
            GitOk fetch -q origin
            $main = (gh run list --branch main --limit 1 --json conclusion | ConvertFrom-Json)[0].conclusion
            Say "main pipeline : $main" $(if ($main -eq "success") { "Green" } else { "Red" })
            $open = (gh pr list --state open --json headRefName | ConvertFrom-Json).headRefName -join ", "
            Say "open PRs      : $(if ($open) { $open } else { 'none' })"
            Run-Python @'
import httpx, os
from dotenv import load_dotenv
load_dotenv(".env")
d = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]}, timeout=20).json()["data"]["free_model_daily_requests"]
print(f"AI calls today: {d['used']} used, {d['remaining']} left of {d['limit']}")
'@
            Say "Ready." "Green"
        }
        "tests" { & $Python -m pytest -q -p no:warnings }
        "break" {
            Assert-Scenario; Assert-Clean
            if (Open-Pr) { throw "$(Branch) already has an open PR. Use 'fix' or 'close'." }
            GitOk fetch -q origin
            git branch -D (Branch) 2>$null | Out-Null
            GitOk switch -q -c (Branch) origin/main
            try {
                Apply-Break
                GitOk add -A app Dockerfile
                GitOk commit -q -m $Scenarios[$Scenario].Title
                GitOk push -q -f -u origin (Branch)
            } finally { git switch -q main }
            gh pr create --head (Branch) --base main --title $Scenarios[$Scenario].Title --body "Live demo scenario $Scenario. Expected to fail at: $($Scenarios[$Scenario].FailsAt). Never merged." | Out-Null
            Say "Pushed a failing change on $(Branch). Expected to fail at: $($Scenarios[$Scenario].FailsAt)" "Yellow"
            Wait-Run
            Say "Open the PR link above -> Conversation tab for the AI diagnosis (refresh if it is not there yet)." "Green"
        }
        "status" { Assert-Scenario; Show-Status }
        "fix" {
            Assert-Scenario; Assert-Clean
            if (-not (Open-Pr)) { throw "No open PR for $(Branch). Run: .\scripts\demo.ps1 break $Scenario" }
            GitOk fetch -q origin
            GitOk switch -q (Branch)
            try {
                GitOk reset -q --hard "origin/$(Branch)"
                $msg = Apply-Fix
                GitOk add -A app Dockerfile
                GitOk commit -q -m $msg
                GitOk push -q origin (Branch)
            } finally { git switch -q main }
            Say "Pushed the fix: $msg" "Yellow"
            Wait-Run
        }
        "close" {
            Assert-Scenario
            $pr = Open-Pr
            if ($pr) {
                gh pr comment $pr --body "Demo complete: failure shown, fix verified green in CI. Closed without merging by design." | Out-Null
                gh pr close $pr --delete-branch 2>$null | Out-Null
                Say "Closed PR #$pr and deleted $(Branch)." "Green"
            } else { Say "No open PR for $(Branch)." }
            git branch -D (Branch) 2>$null | Out-Null
        }
        { $_ -in "before", "after" } {
            docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running" }
            if ($Command -eq "before") { $file = "Dockerfile.baseline"; $tag = "rewriter:before"; $label = "ORIGINAL (before AI optimization)" }
            else { $file = "Dockerfile"; $tag = "rewriter:after"; $label = "AI-OPTIMIZED (after)" }
            $buildArgs = @("build", "-q", "-f", $file, "-t", $tag)
            if ($NoCache) { $buildArgs += "--no-cache" }
            Say "Building the $label image from $file ..." "Yellow"
            $secs = (Measure-Command { & docker @buildArgs . | Out-Null }).TotalSeconds
            if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
            $sizeMb = [math]::Round([double](docker image inspect $tag --format "{{.Size}}") / 1e6, 1)
            $user = docker run --rm --entrypoint id $tag
            Say "Scanning with Trivy (fixable CRITICAL only)..." "Yellow"
            $scan = (trivy image --quiet --scanners vuln --ignore-unfixed --severity CRITICAL --format json $tag 2>$null | Out-String) | ConvertFrom-Json
            $vulns = @($scan.Results | ForEach-Object { $_.Vulnerabilities } | Where-Object { $_ })
            $color = if ($Command -eq "before") { "Red" } else { "Green" }
            Write-Host ""
            Say "==================== $label ====================" $color
            Say ("Dockerfile        : {0}" -f $file)
            Say ("Build time        : {0:N1} s{1}" -f $secs, $(if ($NoCache) { " (no cache)" } else { " (cached layers reused)" }))
            Say ("Image size        : {0} MB" -f $sizeMb) $color
            Say ("Runs as           : {0}" -f $user) $color
            Say ("Fixable CRITICAL  : {0}" -f $vulns.Count) $color
            foreach ($v in $vulns) { Write-Host ("   {0,-14} {1,-16} fixed in {2}" -f $v.PkgName, $v.VulnerabilityID, $v.FixedVersion) -ForegroundColor $color }
            Write-Host ""
            docker images rewriter
        }
        "deploy" {
            Assert-Cluster
            $sha = (gh run list --workflow pipeline --branch main --status success --limit 1 --json headSha | ConvertFrom-Json)[0].headSha
            if (-not $sha) { throw "No successful pipeline run on main found" }
            $img = "$Image`:$sha"
            # 1) Namespace + app manifest (k8s/rewriter-ia2.yaml) with the image set to the latest scanned main build
            $manifest = (Read-Text "k8s/rewriter-ia2.yaml") -replace "image: \S+/personalized-rewriter:\S+", "image: $img"
            $manifest | kubectl --context $Ctx apply -f - | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed" }
            # 2) Secret rewriter-config = k8s/app-config.env + OPENROUTER_API_KEY from .env (temp file, deleted; key never printed)
            # API keys only (OpenRouter and, if present, Google AI Studio); never printed
            $keyLine = @(Get-Content (Join-Path $Root ".env") | Where-Object { $_ -match '^(OPENROUTER_API_KEY|GOOGLE_API_KEY)=.+' })
            if (-not $keyLine) { throw "No API key found in .env" }
            $cfg = @(Get-Content (Join-Path $Root "k8s\app-config.env") | Where-Object { $_ -match '^[A-Z_]+=' })
            $tmp = Join-Path $env:TEMP ("rw-" + [guid]::NewGuid().ToString() + ".env")
            try {
                [IO.File]::WriteAllText($tmp, (($cfg + $keyLine) -join "`n") + "`n", $Utf8)
                Kube create secret generic rewriter-config "--from-env-file=$tmp" --dry-run=client -o yaml | kubectl --context $Ctx apply -f - | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "could not create Secret rewriter-config" }
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            Say "Secret rewriter-config updated (key not shown)."
            # 3) Restart so the Pods pick up the Secret, then wait for 3 ready replicas
            Kube annotate deployment/rewriter "git-sha=$sha" --overwrite | Out-Null
            Kube rollout restart deployment/rewriter | Out-Null
            Say "Rolling out $img ..." "Yellow"
            Kube rollout status deployment/rewriter --timeout=300s
            if ($LASTEXITCODE -ne 0) { throw "rollout did not finish; check: kubectl --context $Ctx -n $Ns get pods" }
            Kube get pods -l app=rewriter -o wide
            # 4) Monitoring stack (scripts/install_ia2_monitoring.py; safe to re-run)
            Say "Installing/updating Prometheus + Grafana ..." "Yellow"
            & $Python (Join-Path $Root "scripts\install_ia2_monitoring.py")
            Say "Deployed. App: $AppUrl   Next: .\scripts\demo.ps1 open / monitor" "Green"
        }
        "open" {
            Start-Process "$AppUrl/"
            Say "App: $AppUrl   (Swagger: $AppUrl/docs)" "Green"
        }
        "monitor" {
            Start-Forward "prometheus" "9090:9090" "Prometheus port-forward"
            Start-Forward "grafana" "3000:3000" "Grafana port-forward"
            Start-Sleep 4
            $sec = Kube get secret ia2-grafana-admin -o json | ConvertFrom-Json
            $pw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($sec.data.'admin-password'))
            Say "Prometheus: http://localhost:9090/targets" "Green"
            Say "Grafana:    http://localhost:3000   user: admin   password: $pw" "Green"
            Say "            Dashboards -> DevOps IA2 -> Personalized Rewriter - IA2" "Green"
        }
        "traffic" {
            $ok = 0; $fail = 0
            Say "Sending $Requests sample+health requests and $Rewrites rewrites to $AppUrl ..." "Yellow"
            $sample = "I like keeping my communication simple and direct. I generally avoid unnecessary wording."
            for ($i = 1; $i -le $Requests; $i++) {
                try {
                    Invoke-RestMethod -Method Post -Uri "$AppUrl/sample" -ContentType "application/json" -Body (@{ user_id = "demo$($i % 5)"; sample = $sample } | ConvertTo-Json) | Out-Null
                    Invoke-RestMethod -Uri "$AppUrl/health" | Out-Null
                    $ok++
                } catch { $fail++ }
                Write-Host -NoNewline "."
            }
            Write-Host ""
            for ($i = 1; $i -le $Rewrites; $i++) {
                try {
                    $r = Invoke-RestMethod -Method Post -Uri "$AppUrl/rewrite" -ContentType "application/json" -Body (@{ user_id = "demo1"; text = "meeting moved to friday`nreport due monday`ninform the team" } | ConvertTo-Json)
                    Say ("  rewrite {0}: {1} ms ({2})" -f $i, $r.latency_ms, $r.model) "DarkGray"
                } catch { Say ("  rewrite {0}: failed ({1})" -f $i, $_.ErrorDetails.Message) "DarkYellow" }
                Start-Sleep 3
            }
            Say "Done: $ok ok, $fail failed." $(if ($fail -eq 0) { "Green" } else { "Yellow" })
        }
        "heal" {
            Assert-Cluster
            Say "Pods BEFORE:" "Yellow"; Kube get pods -l app=rewriter
            $victim = (Kube get pods -l app=rewriter -o jsonpath="{.items[0].metadata.name}")
            Say "Deleting $victim (simulating a crash)..." "Red"
            Kube delete pod $victim --wait=false | Out-Null
            for ($t = 0; $t -lt 45; $t++) {
                Start-Sleep 2
                $ready = @(Kube get pods -l app=rewriter -o jsonpath="{range .items[*]}{.status.containerStatuses[0].ready}{'\n'}{end}" | Where-Object { $_ -eq "true" }).Count
                if ($ready -ge 3 -and -not ((Kube get pods -l app=rewriter -o name) -match [regex]::Escape($victim))) { break }
            }
            Say "Pods AFTER (a new Pod replaced $victim):" "Green"; Kube get pods -l app=rewriter
        }
        "compare" {
            Run-Python @'
import json
b = json.load(open("docs/evidence/docker_baseline.json")); o = json.load(open("docs/evidence/docker_optimized.json"))
rows = [("Image size (MB)", "image_size_mb"), ("Cold build (s)", "cold_build_s"), ("Warm build (s)", "warm_build_s"),
        ("Filesystem layers", "filesystem_layers"), ("Startup to healthy (s)", "startup_to_healthy_s"), ("Runtime UID", "runtime_uid")]
print(f"{'Metric':26}{'Baseline':>12}{'Optimized':>12}")
for label, k in rows:
    print(f"{label:26}{str(b[k]):>12}{str(o[k]):>12}")
for sev in ("CRITICAL", "HIGH"):
    print(f"{'Fixable ' + sev:26}{b['trivy']['fixable'][sev]:>12}{o['trivy']['fixable'][sev]:>12}")
'@
            docker images personalized-rewriter
        }
    }
} catch {
    Say "ERROR: $_" "Red"
    exit 1
}
