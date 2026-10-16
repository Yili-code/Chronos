[CmdletBinding()]
param(
    [string]$ProjectId = "yili-chronos-prod",
    [string]$Region = "asia-east1"
)

$ErrorActionPreference = "Stop"
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path -LiteralPath $gcloud)) { throw "Google Cloud CLI is required" }

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:gcloud @Arguments
    if ($LASTEXITCODE -ne 0) { throw "gcloud command failed" }
}

function Read-SecretValue([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Publish-Secret([string]$Name, [string]$Value) {
    $previousErrorAction = $ErrorActionPreference
    try {
        # A missing secret is the expected first-run state, not a terminating error.
        $ErrorActionPreference = "Continue"
        & $script:gcloud secrets describe $Name --project $ProjectId --quiet 2>$null | Out-Null
        $exists = $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if (-not $exists) {
        Invoke-Gcloud secrets create $Name --project $ProjectId --replication-policy user-managed --locations $Region --quiet | Out-Null
    }
    $file = New-TemporaryFile
    try {
        [IO.File]::WriteAllText($file.FullName, $Value, [Text.UTF8Encoding]::new($false))
        Invoke-Gcloud secrets versions add $Name --project $ProjectId --data-file $file.FullName --quiet | Out-Null
    }
    finally {
        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
    }
}

$username = Read-SecretValue "NTOU CAS username (hidden)"
$password = Read-SecretValue "NTOU CAS password (hidden)"
if ([string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($password)) {
    throw "Both CAS values are required"
}
try {
    Publish-Secret "chronos-tronclass-cas-username" $username
    Publish-Secret "chronos-tronclass-cas-password" $password
}
finally {
    $username = ""
    $password = ""
}
Write-Output "CAS credentials stored in the two dedicated Secret Manager secrets."
