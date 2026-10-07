[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "asia-east1",
    [string]$Service = "chronos",
    [string]$GeminiModel = "gemini-3.8-flash",
    [switch]$EnableGmail
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7 or later is required. Run this script with pwsh -File."
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
$runtimeAccountId = "chronos-runtime"
$runtimeAccount = "$runtimeAccountId@$ProjectId.iam.gserviceaccount.com"
$installedGcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$gcloudCommand = if (Test-Path -LiteralPath $installedGcloud) {
    $installedGcloud
}
else {
    (Get-Command gcloud -ErrorAction SilentlyContinue).Source
}

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:gcloudCommand @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud command failed: $($Arguments -join ' ')"
    }
}

function Get-DotEnvValue {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $envFile)) { return "" }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)$") {
            $value = $matches[1].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return ""
}

function Read-RequiredSecret {
    param([string]$EnvironmentName, [string]$Prompt)
    $value = [Environment]::GetEnvironmentVariable($EnvironmentName)
    if ([string]::IsNullOrWhiteSpace($value)) { $value = Get-DotEnvValue $EnvironmentName }
    if ([string]::IsNullOrWhiteSpace($value)) {
        $secure = Read-Host $Prompt -AsSecureString
        $value = [System.Net.NetworkCredential]::new("", $secure).Password
    }
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$EnvironmentName is required" }
    return $value
}

function Get-OptionalLocalValue {
    param([string]$EnvironmentName)
    $value = [Environment]::GetEnvironmentVariable($EnvironmentName)
    if ([string]::IsNullOrWhiteSpace($value)) { $value = Get-DotEnvValue $EnvironmentName }
    return $value
}

function Get-OrCreateGeminiApiKey {
    $localValue = Get-OptionalLocalValue "CHRONOS_GEMINI_API_KEY"
    if (-not [string]::IsNullOrWhiteSpace($localValue)) { return $localValue }

    $keyListJson = (& $script:gcloudCommand services api-keys list --project $ProjectId `
        --format json 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "Unable to list Gemini API keys" }
    $keyList = if ([string]::IsNullOrWhiteSpace($keyListJson)) { @() } else { @($keyListJson | ConvertFrom-Json) }
    $keyName = $keyList |
        Where-Object { $_.displayName -eq "Chronos Cloud Run Gemini" -and [string]::IsNullOrWhiteSpace($_.deleteTime) } |
        Select-Object -First 1 -ExpandProperty name
    if ([string]::IsNullOrWhiteSpace($keyName)) {
        & $script:gcloudCommand services api-keys create --project $ProjectId `
            --display-name "Chronos Cloud Run Gemini" `
            --api-target "service=generativelanguage.googleapis.com" --format none --quiet 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to create a dedicated Gemini API key"
        }
        $keyListJson = (& $script:gcloudCommand services api-keys list --project $ProjectId `
            --format json 2>$null) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw "Unable to list the created Gemini API key" }
        $keyList = if ([string]::IsNullOrWhiteSpace($keyListJson)) { @() } else { @($keyListJson | ConvertFrom-Json) }
        $keyName = $keyList |
            Where-Object { $_.displayName -eq "Chronos Cloud Run Gemini" -and [string]::IsNullOrWhiteSpace($_.deleteTime) } |
            Select-Object -First 1 -ExpandProperty name
        if ([string]::IsNullOrWhiteSpace($keyName)) { throw "Created Gemini API key was not found" }
    }
    $keyString = (& $script:gcloudCommand services api-keys get-key-string $keyName.Trim() `
        --project $ProjectId --format "value(keyString)" --quiet 2>$null | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($keyString)) {
        throw "Unable to retrieve the dedicated Gemini API key"
    }
    return $keyString.Trim()
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Get-OrCreateRandomSecret {
    param([string]$Name)
    & $script:gcloudCommand secrets describe $Name --project $ProjectId --quiet 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $value = (& $script:gcloudCommand secrets versions access latest --secret $Name `
            --project $ProjectId 2>$null) -join "`n"
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    return (New-RandomSecret)
}

function Set-CloudSecret {
    param([string]$Name, [string]$Value)
    & $script:gcloudCommand secrets describe $Name --project $ProjectId --quiet 2>$null | Out-Null
    $exists = $LASTEXITCODE -eq 0
    if (-not $exists) {
        Invoke-Gcloud secrets create $Name --replication-policy automatic --project $ProjectId --quiet | Out-Null
    }
    else {
        $currentValue = (& $script:gcloudCommand secrets versions access latest --secret $Name `
            --project $ProjectId 2>$null) -join "`n"
        if ($LASTEXITCODE -eq 0 -and $currentValue.Trim() -ceq $Value) {
            $versionName = (& $script:gcloudCommand secrets versions describe latest --secret $Name `
                --project $ProjectId --format "value(name)" --quiet 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($versionName)) {
                Invoke-Gcloud secrets add-iam-policy-binding $Name --project $ProjectId `
                    --member "serviceAccount:$runtimeAccount" --role roles/secretmanager.secretAccessor --quiet | Out-Null
                return $versionName.Trim().Split('/')[-1]
            }
        }
    }
    $temporaryFile = New-TemporaryFile
    try {
        [System.IO.File]::WriteAllText($temporaryFile.FullName, $Value, [System.Text.UTF8Encoding]::new($false))
        $versionName = & $script:gcloudCommand secrets versions add $Name --data-file $temporaryFile.FullName `
            --project $ProjectId --format "value(name)" --quiet
        if ($LASTEXITCODE -ne 0) { throw "Unable to add a version for $Name" }
    }
    finally {
        Remove-Item -LiteralPath $temporaryFile.FullName -Force -ErrorAction SilentlyContinue
    }
    $version = ($versionName | Select-Object -Last 1).Trim().Split('/')[-1]
    Invoke-Gcloud secrets add-iam-policy-binding $Name --project $ProjectId `
        --member "serviceAccount:$runtimeAccount" --role roles/secretmanager.secretAccessor --quiet | Out-Null
    return $version
}

if ([string]::IsNullOrWhiteSpace($gcloudCommand)) {
    throw "Google Cloud CLI is required. Install it and run gcloud auth login first."
}

$telegramBotToken = Read-RequiredSecret "CHRONOS_TELEGRAM_BOT_TOKEN" "Telegram bot token"
$telegramChatId = Read-RequiredSecret "CHRONOS_TELEGRAM_CHAT_ID" "Telegram chat ID"

Invoke-Gcloud config set project $ProjectId --quiet
Invoke-Gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com `
    firestore.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com `
    iam.googleapis.com apikeys.googleapis.com generativelanguage.googleapis.com --project $ProjectId --quiet
$geminiApiKey = Get-OrCreateGeminiApiKey
$telegramWebhookSecret = Get-OrCreateRandomSecret "chronos-telegram-webhook-secret"
$schedulerSecret = Get-OrCreateRandomSecret "chronos-scheduler-secret"
$webPassword = Get-OrCreateRandomSecret "chronos-web-password"

& $gcloudCommand iam service-accounts describe $runtimeAccount --project $ProjectId --quiet 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud iam service-accounts create $runtimeAccountId --project $ProjectId `
        --display-name "Chronos Cloud Run runtime" --quiet
}
Invoke-Gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$runtimeAccount" `
    --role roles/datastore.user --condition=None --quiet

& $gcloudCommand firestore databases describe --database "(default)" --project $ProjectId --quiet 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud firestore databases create --database "(default)" --location $Region `
        --type firestore-native --delete-protection --project $ProjectId --quiet
}

$secretVersions = @{}
$secretVersions["gemini"] = Set-CloudSecret "chronos-gemini-api-key" $geminiApiKey
$secretVersions["telegramToken"] = Set-CloudSecret "chronos-telegram-bot-token" $telegramBotToken
$secretVersions["telegramChat"] = Set-CloudSecret "chronos-telegram-chat-id" $telegramChatId
$secretVersions["telegramWebhook"] = Set-CloudSecret "chronos-telegram-webhook-secret" $telegramWebhookSecret
$secretVersions["scheduler"] = Set-CloudSecret "chronos-scheduler-secret" $schedulerSecret
$secretVersions["webPassword"] = Set-CloudSecret "chronos-web-password" $webPassword

$secretBindings = @(
    "CHRONOS_GEMINI_API_KEY=chronos-gemini-api-key:$($secretVersions.gemini)",
    "CHRONOS_TELEGRAM_BOT_TOKEN=chronos-telegram-bot-token:$($secretVersions.telegramToken)",
    "CHRONOS_TELEGRAM_CHAT_ID=chronos-telegram-chat-id:$($secretVersions.telegramChat)",
    "CHRONOS_TELEGRAM_WEBHOOK_SECRET=chronos-telegram-webhook-secret:$($secretVersions.telegramWebhook)",
    "CHRONOS_SCHEDULER_SECRET=chronos-scheduler-secret:$($secretVersions.scheduler)",
    "CHRONOS_WEB_PASSWORD=chronos-web-password:$($secretVersions.webPassword)"
)
$secondaryGeminiKey = Get-OptionalLocalValue "CHRONOS_GEMINI_API_KEY_SECONDARY"
if (-not [string]::IsNullOrWhiteSpace($secondaryGeminiKey)) {
    $secondaryVersion = Set-CloudSecret "chronos-gemini-api-key-secondary" $secondaryGeminiKey
    $secretBindings += "CHRONOS_GEMINI_API_KEY_SECONDARY=chronos-gemini-api-key-secondary:$secondaryVersion"
}
else {
    # Keep an already configured secondary when the local .env omits it.
    $existingServiceJson = (& $gcloudCommand run services describe $Service --region $Region --project $ProjectId `
        --format json --quiet 2>$null) -join "`n"
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($existingServiceJson)) {
        $existingService = $existingServiceJson | ConvertFrom-Json
        $secondaryBinding = $existingService.spec.template.spec.containers[0].env |
            Where-Object { $_.name -eq "CHRONOS_GEMINI_API_KEY_SECONDARY" } | Select-Object -First 1
        $secondaryRef = $secondaryBinding.valueFrom.secretKeyRef
        if ($secondaryRef -and $secondaryRef.name -and $secondaryRef.key) {
            $secretBindings += "CHRONOS_GEMINI_API_KEY_SECONDARY=$($secondaryRef.name):$($secondaryRef.key)"
        }
    }
}
$keyCooldown = Get-OptionalLocalValue "CHRONOS_GEMINI_KEY_COOLDOWN_SECONDS"
if ([string]::IsNullOrWhiteSpace($keyCooldown)) { $keyCooldown = "60" }
$parsedKeyCooldown = 0
if (-not [int]::TryParse($keyCooldown, [ref]$parsedKeyCooldown) -or $parsedKeyCooldown -lt 1 -or $parsedKeyCooldown -gt 86400) {
    throw "CHRONOS_GEMINI_KEY_COOLDOWN_SECONDS must be an integer between 1 and 86400"
}
if ($EnableGmail) {
    # OAuth was completed locally. Only Secret Manager receives the credentials.
    foreach ($suffix in @("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "ACCOUNT")) {
        $environmentName = "CHRONOS_GMAIL_$suffix"
        $value = Get-OptionalLocalValue $environmentName
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "$environmentName is missing. Run the Gmail authorization helper before deploying."
        }
        $secretName = "chronos-gmail-" + $suffix.ToLowerInvariant().Replace('_', '-')
        $version = Set-CloudSecret $secretName $value
        $secretBindings += "${environmentName}=${secretName}:$version"
    }
    $keepSenders = Get-OptionalLocalValue "CHRONOS_GMAIL_KEEP_SENDERS"
    if (-not [string]::IsNullOrWhiteSpace($keepSenders)) {
        $version = Set-CloudSecret "chronos-gmail-keep-senders" $keepSenders
        $secretBindings += "CHRONOS_GMAIL_KEEP_SENDERS=chronos-gmail-keep-senders:$version"
    }
    Invoke-Gcloud services enable gmail.googleapis.com --project $ProjectId --quiet
}
$environment = @(
    "CHRONOS_DATABASE_BACKEND=firestore",
    "CHRONOS_FIRESTORE_PROJECT_ID=$ProjectId",
    "CHRONOS_FIRESTORE_DATABASE=(default)",
    "CHRONOS_FIRESTORE_COLLECTION_PREFIX=chronos",
    "CHRONOS_GEMINI_MODEL=$GeminiModel",
    "CHRONOS_GEMINI_KEY_COOLDOWN_SECONDS=$parsedKeyCooldown",
    "CHRONOS_ENABLE_INTERNAL_SCHEDULER=false",
    "CHRONOS_ENABLE_GMAIL=$($EnableGmail.IsPresent.ToString().ToLowerInvariant())",
    "CHRONOS_TIMEZONE=Asia/Taipei",
    "CHRONOS_WEB_USERNAME=chronos"
) -join ','

Push-Location $projectRoot
try {
    Invoke-Gcloud run deploy $Service --source . --region $Region --project $ProjectId `
        --service-account $runtimeAccount --allow-unauthenticated --port 8080 `
        --memory 512Mi --cpu 1 --concurrency 20 --max-instances 3 --timeout 1800 `
        --set-env-vars $environment --set-secrets ($secretBindings -join ',') --quiet
}
finally {
    Pop-Location
}

$serviceUrl = (& $gcloudCommand run services describe $Service --region $Region --project $ProjectId `
    --format "value(status.url)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($serviceUrl)) {
    throw "Cloud Run did not return a service URL"
}
Invoke-Gcloud run services update $Service --region $Region --project $ProjectId `
    --update-env-vars "CHRONOS_PUBLIC_BASE_URL=$serviceUrl" --quiet

$jobName = "$Service-daily-tasks"
$dailyUri = "$serviceUrl/internal/daily"
& $gcloudCommand scheduler jobs describe $jobName --location $Region --project $ProjectId --quiet 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Invoke-Gcloud scheduler jobs update http $jobName --location $Region --project $ProjectId `
        --schedule "0 8 * * *" --time-zone "Asia/Taipei" --uri $dailyUri --http-method POST `
        --update-headers "X-Chronos-Scheduler-Secret=$schedulerSecret" --max-retry-attempts 3 --attempt-deadline 1800s --quiet | Out-Null
}
else {
    Invoke-Gcloud scheduler jobs create http $jobName --location $Region --project $ProjectId `
        --schedule "0 8 * * *" --time-zone "Asia/Taipei" --uri $dailyUri --http-method POST `
        --headers "X-Chronos-Scheduler-Secret=$schedulerSecret" --max-retry-attempts 3 --attempt-deadline 1800s --quiet | Out-Null
}

if ($EnableGmail) {
    $mailJobName = "$Service-daily-mail"
    $mailUri = "$serviceUrl/internal/mail/daily"
    & $gcloudCommand scheduler jobs describe $mailJobName --location $Region --project $ProjectId --quiet 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Invoke-Gcloud scheduler jobs update http $mailJobName --location $Region --project $ProjectId `
            --schedule "0 7 * * *" --time-zone "Asia/Taipei" --uri $mailUri --http-method POST `
            --update-headers "X-Chronos-Scheduler-Secret=$schedulerSecret" --max-retry-attempts 3 --attempt-deadline 1800s --quiet | Out-Null
    }
    else {
        Invoke-Gcloud scheduler jobs create http $mailJobName --location $Region --project $ProjectId `
            --schedule "0 7 * * *" --time-zone "Asia/Taipei" --uri $mailUri --http-method POST `
            --headers "X-Chronos-Scheduler-Secret=$schedulerSecret" --max-retry-attempts 3 --attempt-deadline 1800s --quiet | Out-Null
    }
}

$health = Invoke-RestMethod -Uri "$serviceUrl/health" -Method Get -TimeoutSec 30
if ($health.status -ne "ok") { throw "Cloud Run health check failed" }
$webhook = Invoke-RestMethod -Uri "https://api.telegram.org/bot$telegramBotToken/getWebhookInfo" `
    -Method Get -TimeoutSec 30
if (-not $webhook.ok -or $webhook.result.url -ne "$serviceUrl/telegram/webhook") {
    throw "Telegram webhook was not registered to the Cloud Run service"
}

Write-Output "Chronos deployed successfully."
Write-Output "Service URL: $serviceUrl"
Write-Output "Health: ok"
Write-Output "Telegram webhook: configured"
Write-Output "Web username: chronos"
Write-Output "Web password is stored in Secret Manager as chronos-web-password."
