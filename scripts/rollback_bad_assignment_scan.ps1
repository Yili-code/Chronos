[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Start,
    [Parameter(Mandatory = $true)][string]$End,
    [string]$ProjectId = "yili-chronos-prod",
    [string]$Region = "asia-east1",
    [switch]$Apply,
    [string]$ConfirmPlanHash = ""
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "cloud_job_release.ps1")
if ($Start -notmatch '^\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-][0-9:]+)$' -or
    $End -notmatch '^\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-][0-9:]+)$') { throw "Timezone-aware timestamps are required" }
if ($Apply -and $ConfirmPlanHash -notmatch '^[a-f0-9]{64}$') { throw "Preview first, then supply its exact plan hash" }
$root = Split-Path -Parent $PSScriptRoot
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$source = New-ChronosJobSource -Root $root -Dockerfile "Dockerfile.assignment-rollback"
try {
    $applyValue = $Apply.IsPresent.ToString().ToLowerInvariant()
    & $gcloud run jobs deploy chronos-assignment-rollback --source $source.Directory --project $ProjectId --region $Region `
        --service-account "chronos-collector@$ProjectId.iam.gserviceaccount.com" --memory 512Mi --max-retries 0 --task-timeout 300s `
        --set-env-vars "CHRONOS_DATABASE_BACKEND=firestore,CHRONOS_FIRESTORE_PROJECT_ID=$ProjectId,CHRONOS_FIRESTORE_COLLECTION_PREFIX=chronos,CHRONOS_ASSIGNMENT_ROLLBACK_START=$Start,CHRONOS_ASSIGNMENT_ROLLBACK_END=$End,CHRONOS_ASSIGNMENT_ROLLBACK_APPLY=$applyValue,CHRONOS_ASSIGNMENT_ROLLBACK_CONFIRM=$ConfirmPlanHash" `
        --set-secrets "CHRONOS_TELEGRAM_BOT_TOKEN=chronos-telegram-bot-token:latest,CHRONOS_TELEGRAM_CHAT_ID=chronos-telegram-chat-id:latest" `
        --labels "purpose=assignment-rollback,commit-sha=$($source.Sha)" --quiet
    if ($LASTEXITCODE -ne 0) { throw "Rollback job deployment failed" }
    & $gcloud run jobs execute chronos-assignment-rollback --project $ProjectId --region $Region --wait --quiet
    if ($LASTEXITCODE -ne 0) { throw "Rollback execution failed" }
}
finally { Remove-ChronosJobSource $source }
