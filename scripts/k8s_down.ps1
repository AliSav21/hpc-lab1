# Крок 2: повне видалення кластера разом із томами, образами і моніторингом.
[CmdletBinding()]
param([string]$Cluster = "hpc")

$ErrorActionPreference = "Stop"
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) { throw "у PATH немає kind" }
& kind delete cluster --name $Cluster
