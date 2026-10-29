# Крок 2 лабораторної роботи 3: одна команда розгортання стенду в kind.
# Створює кластер, ставить мітку role=infra, збирає і завантажує образ, створює Secret з .env,
# застосовує маніфести і ставить моніторинг.
#
# Усі звернення до кластера йдуть з явним --context kind-<Cluster>: без цього kubectl пішов би
# у поточний контекст kubeconfig, тобто в будь-який сторонній кластер.
[CmdletBinding()]
param(
    [string]$Cluster = "hpc",
    [string]$Image = "donor-registry:lab3",
    [switch]$SkipMonitoring
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$ctx = "kind-$Cluster"

function Need-Tool($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "у PATH немає $name"
    }
}
# $ErrorActionPreference не діє на зовнішні програми: код виходу перевіряємо самі
function Invoke-Checked($file, $arguments, $what) {
    & $file @arguments
    if ($LASTEXITCODE -ne 0) { throw "$what завершилося з кодом $LASTEXITCODE" }
}
function Invoke-Kubectl($arguments) {
    # kubectl.exe з розширенням: інакше ім'я функції перекриває саму програму
    & kubectl.exe --context $ctx @arguments
    if ($LASTEXITCODE -ne 0) { throw "kubectl $($arguments -join ' ') завершився з кодом $LASTEXITCODE" }
}

foreach ($t in @("docker", "kind", "kubectl", "helm")) { Need-Tool $t }

# 0. пароль бази беремо з .env: в образі, ConfigMap і репозиторії його немає
if (-not (Test-Path .env)) {
    throw ".env немає: copy .env.example .env і задати POSTGRES_PASSWORD"
}
$m = Select-String -Path .env -Pattern '^\s*POSTGRES_PASSWORD\s*=\s*(.+?)\s*$' | Select-Object -First 1
if (-not $m) { throw "у .env немає рядка POSTGRES_PASSWORD" }
$pw = $m.Matches[0].Groups[1].Value
if ($pw -eq "change-me") { throw "у .env лишився POSTGRES_PASSWORD=change-me" }

# 1. кластер
# kind пише "No kind clusters found." у stderr; у Windows PowerShell 5.1 такий рядок стає
# помилкою і зупиняв би скрипт, тому на час виклику знімаємо зупинку
$ErrorActionPreference = "Continue"
$clusters = @(& kind get clusters 2>&1 | ForEach-Object { "$_".Trim() })
$ErrorActionPreference = "Stop"
if ($clusters -notcontains $Cluster) {
    Write-Host "== створюю кластер $Cluster"
    Invoke-Checked "kind" @("create", "cluster", "--config", "k8s/kind-cluster.yaml") "kind create cluster"
} else {
    Write-Host "== кластер $Cluster вже існує"
}

# запобіжник: далі працюємо лише з цим кластером
& kubectl config use-context $ctx | Out-Null
$current = & kubectl config current-context
if ($current -ne $ctx) {
    throw "поточний контекст '$current', а не '$ctx': розгортання зупинено"
}

# 2. мітка role=infra на control-plane: на цей вузол ідуть база і моніторинг
$cp = & kubectl --context $ctx get nodes -l node-role.kubernetes.io/control-plane -o jsonpath="{.items[0].metadata.name}"
if (-not $cp) { throw "у кластері $Cluster немає вузла control-plane" }
Invoke-Kubectl @("label", "node", $cp, "role=infra", "--overwrite")

# 3. образ сервісу в кластер (реєстру немає)
Write-Host "== збираю образ $Image"
Invoke-Checked "docker" @("build", "-t", $Image, ".") "docker build"
Invoke-Checked "kind" @("load", "docker-image", $Image, "--name", $Cluster) "kind load docker-image"

# 4. namespace, Secret, схема і маніфести
Invoke-Kubectl @("apply", "-f", "k8s/base/00-namespace.yaml")
& kubectl --context $ctx create secret generic db-secret -n donors --from-literal=POSTGRES_PASSWORD=$pw `
    --dry-run=client -o yaml | kubectl --context $ctx apply -f -
& kubectl --context $ctx create configmap db-schema -n donors --from-file=schema.sql=db/schema.sql `
    --dry-run=client -o yaml | kubectl --context $ctx apply -f -
# Job незмінний після створення, тому перед повторним застосуванням його видаляємо
Invoke-Kubectl @("delete", "job", "db-migrate", "-n", "donors", "--ignore-not-found")
Invoke-Kubectl @("apply", "-f", "k8s/base")

Write-Host "== чекаю базу і міграцію"
Invoke-Kubectl @("rollout", "status", "statefulset/db", "-n", "donors", "--timeout=300s")
Invoke-Kubectl @("wait", "--for=condition=complete", "job/db-migrate", "-n", "donors", "--timeout=300s")
Invoke-Kubectl @("rollout", "status", "deployment/web", "-n", "donors", "--timeout=300s")

# 5. моніторинг
if (-not $SkipMonitoring) {
    Write-Host "== metrics-server"
    Invoke-Kubectl @("apply", "-f", "https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml")
    # у kind kubelet віддає метрики з самопідписаним сертифікатом
    $argsNow = & kubectl --context $ctx get deployment metrics-server -n kube-system -o jsonpath="{.spec.template.spec.containers[0].args}"
    if ($argsNow -notlike "*kubelet-insecure-tls*") {
        Invoke-Kubectl @("patch", "deployment", "metrics-server", "-n", "kube-system", "--type=json",
                             "--patch-file", "k8s/monitoring/metrics-server-insecure-tls.patch.json")
    }
    Invoke-Kubectl @("rollout", "status", "deployment/metrics-server", "-n", "kube-system", "--timeout=300s")

    Write-Host "== kube-prometheus-stack і postgres-exporter"
    & helm repo add prometheus-community https://prometheus-community.github.io/helm-charts | Out-Null
    & helm repo update prometheus-community | Out-Null
    Invoke-Checked "helm" @("upgrade", "--install", "monitoring", "prometheus-community/kube-prometheus-stack",
        "--kube-context", $ctx, "-n", "monitoring", "--create-namespace",
        "-f", "k8s/monitoring/kube-prometheus-stack.values.yaml", "--wait", "--timeout", "15m") "helm kube-prometheus-stack"
    Invoke-Checked "helm" @("upgrade", "--install", "pg-exporter", "prometheus-community/prometheus-postgres-exporter",
        "--kube-context", $ctx, "-n", "donors",
        "-f", "k8s/monitoring/postgres-exporter.values.yaml", "--wait", "--timeout", "5m") "helm postgres-exporter"
    Invoke-Kubectl @("apply", "-f", "k8s/monitoring/web-podmonitor.yaml")
}

Write-Host ""
Write-Host "стенд готовий:"
Write-Host "  сервіс     http://127.0.0.1:18080/donor-registry"
Write-Host "  Prometheus http://127.0.0.1:19090"
Invoke-Kubectl @("get", "pods", "-n", "donors", "-o", "wide")
