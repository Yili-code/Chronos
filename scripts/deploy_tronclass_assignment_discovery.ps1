[CmdletBinding()]
param(
    [string]$ProjectId = "yili-chronos-prod",
    [string]$Region = "asia-east1",
    [string]$Job = "chronos-assignment-discovery",
    [switch]$ExecuteNow
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "cloud_job_release.ps1")
$root = Split-Path -Parent $PSScriptRoot
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$account = "chronos-collector@$ProjectId.iam.gserviceaccount.com"
if (-not (Test-Path -LiteralPath $gcloud)) { throw "Google Cloud CLI is required" }

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:gcloud @Arguments
    if ($LASTEXITCODE -ne 0) { throw "gcloud command failed" }
}

foreach ($secret in @("chronos-tronclass-cas-username", "chronos-tronclass-cas-password")) {
    Invoke-Gcloud secrets describe $secret --project $ProjectId --quiet | Out-Null
    Invoke-Gcloud secrets add-iam-policy-binding $secret --project $ProjectId --member "serviceAccount:$account" --role roles/secretmanager.secretAccessor --quiet | Out-Null
}

$source = New-ChronosJobSource -Root $root -Dockerfile "Dockerfile.assignment-discovery"
$directory = $source.Directory
try {
    Invoke-Gcloud run jobs deploy $Job --source $directory --project $ProjectId --region $Region `
        --service-account $account --memory 512Mi --cpu 1 --tasks 1 --parallelism 1 --max-retries 0 --task-timeout 120s `
        --set-secrets "CHRONOS_TRONCLASS_USERNAME=chronos-tronclass-cas-username:latest,CHRONOS_TRONCLASS_PASSWORD=chronos-tronclass-cas-password:latest" `
        --labels "purpose=assignment-discovery,commit-sha=$($source.Sha)" --quiet
}
finally {
    Remove-ChronosJobSource $source
}
if ($ExecuteNow) {
    Invoke-Gcloud run jobs execute $Job --project $ProjectId --region $Region --wait --quiet
}
Write-Output "Cloud Run assignment discovery completed."
