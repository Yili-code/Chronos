[CmdletBinding()]
param(
    [string]$ProjectId = "yili-chronos-prod",
    [string]$Region = "asia-east1",
    [string]$Job = "chronos-assignment-scan",
    [string]$SchedulerJob = "chronos-tronclass-weekly-scan",
    [switch]$EnableSchedule,
    [switch]$ExecuteNow
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "cloud_job_release.ps1")
$root = Split-Path -Parent $PSScriptRoot
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$collector = "chronos-collector@$ProjectId.iam.gserviceaccount.com"
$schedulerId = "chronos-assignment-scheduler"
$scheduler = "$schedulerId@$ProjectId.iam.gserviceaccount.com"
if (-not (Test-Path -LiteralPath $gcloud)) { throw "Google Cloud CLI is required" }

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:gcloud @Arguments
    if ($LASTEXITCODE -ne 0) { throw "gcloud command failed" }
}

# The collector is isolated from the public Cloud Run service. It reads only
# the four secrets needed for CAS login and Telegram delivery, and Firestore.
foreach ($secret in @(
    "chronos-tronclass-cas-username", "chronos-tronclass-cas-password",
    "chronos-telegram-bot-token", "chronos-telegram-chat-id"
)) {
    Invoke-Gcloud secrets describe $secret --project $ProjectId --quiet | Out-Null
    Invoke-Gcloud secrets add-iam-policy-binding $secret --project $ProjectId `
        --member "serviceAccount:$collector" --role roles/secretmanager.secretAccessor --quiet | Out-Null
}
# The collector's project-level roles/datastore.user binding is provisioned with
# the Chronos runtime. Verify it before this deployment instead of rewriting
# project IAM on every scanner release.
$collectorRole = (& $gcloud projects get-iam-policy $ProjectId --flatten "bindings[].members" `
    --filter "bindings.members:serviceAccount:$collector AND bindings.role:roles/datastore.user" `
    --format "value(bindings.role)" --quiet) -join "`n"
if ($LASTEXITCODE -ne 0 -or $collectorRole.Trim() -ne "roles/datastore.user") {
    throw "Collector requires its existing roles/datastore.user binding"
}
Write-Output "Collector permissions verified."

$source = New-ChronosJobSource -Root $root -Dockerfile "Dockerfile.assignment-scan"
$directory = $source.Directory
try {
    Write-Output "Deploying isolated assignment scanner Job."
    Invoke-Gcloud run jobs deploy $Job --source $directory --project $ProjectId --region $Region `
        --service-account $collector --memory 512Mi --cpu 1 --tasks 1 --parallelism 1 --max-retries 0 --task-timeout 300s `
        --set-env-vars "CHRONOS_DATABASE_BACKEND=firestore,CHRONOS_FIRESTORE_PROJECT_ID=$ProjectId,CHRONOS_FIRESTORE_DATABASE=(default),CHRONOS_FIRESTORE_COLLECTION_PREFIX=chronos,CHRONOS_TIMEZONE=Asia/Taipei,CHRONOS_RELEASE_SHA=$($source.Sha)" `
        --set-secrets "CHRONOS_TRONCLASS_USERNAME=chronos-tronclass-cas-username:latest,CHRONOS_TRONCLASS_PASSWORD=chronos-tronclass-cas-password:latest,CHRONOS_TELEGRAM_BOT_TOKEN=chronos-telegram-bot-token:latest,CHRONOS_TELEGRAM_CHAT_ID=chronos-telegram-chat-id:latest" `
        --labels "purpose=tronclass-assignment-scan,commit-sha=$($source.Sha)" --quiet
}
finally {
    Remove-ChronosJobSource $source
}

if ($ExecuteNow) {
    Invoke-Gcloud run jobs execute $Job --project $ProjectId --region $Region --wait --quiet
}

if ($EnableSchedule) {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $gcloud iam service-accounts describe $scheduler --project $ProjectId --quiet 2>$null | Out-Null
    $schedulerExists = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previousErrorActionPreference
    if (-not $schedulerExists) {
        Invoke-Gcloud iam service-accounts create $schedulerId --project $ProjectId `
            --display-name "Chronos assignment scan scheduler" --quiet
    }
    Invoke-Gcloud run jobs add-iam-policy-binding $Job --project $ProjectId --region $Region `
        --member "serviceAccount:$scheduler" --role roles/run.invoker --quiet | Out-Null
    $uri = "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$Job`:run"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $gcloud scheduler jobs describe $SchedulerJob --project $ProjectId --location $Region --quiet 2>$null | Out-Null
    $schedulerJobExists = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previousErrorActionPreference
    if ($schedulerJobExists) {
        Invoke-Gcloud scheduler jobs update http $SchedulerJob --project $ProjectId --location $Region `
            --schedule "0 6 * * *" --time-zone "Asia/Taipei" --uri $uri --http-method POST `
            --oauth-service-account-email $scheduler --max-retry-attempts 0 --attempt-deadline 300s --quiet | Out-Null
    }
    else {
        Invoke-Gcloud scheduler jobs create http $SchedulerJob --project $ProjectId --location $Region `
            --schedule "0 6 * * *" --time-zone "Asia/Taipei" --uri $uri --http-method POST `
            --oauth-service-account-email $scheduler --max-retry-attempts 0 --attempt-deadline 300s --quiet | Out-Null
    }
    $scheduleJson = (& $gcloud scheduler jobs describe $SchedulerJob --project $ProjectId --location $Region `
        --format "json(state,schedule,timeZone,httpTarget.uri)" --quiet) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "Could not verify scanner schedule" }
    $schedule = $scheduleJson | ConvertFrom-Json
    if ($schedule.state -eq "PAUSED") {
        Invoke-Gcloud scheduler jobs resume $SchedulerJob --project $ProjectId --location $Region --quiet | Out-Null
        $scheduleJson = (& $gcloud scheduler jobs describe $SchedulerJob --project $ProjectId --location $Region `
            --format "json(state,schedule,timeZone,httpTarget.uri)" --quiet) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw "Could not verify resumed scanner schedule" }
        $schedule = $scheduleJson | ConvertFrom-Json
    }
    if ($schedule.state -ne "ENABLED" -or $schedule.schedule -ne "0 6 * * *" -or
        $schedule.timeZone -ne "Asia/Taipei" -or $schedule.httpTarget.uri -ne $uri) {
        throw "Scanner schedule does not match the requested configuration"
    }
    Write-Output "Daily 06:00 Asia/Taipei assignment scheduler is enabled."
}
else {
    Write-Output "Assignment scan Job deployed without changing its daily scheduler."
}
