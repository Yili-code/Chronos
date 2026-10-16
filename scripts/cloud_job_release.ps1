function New-ChronosJobSource {
    param([string]$Root, [string]$Dockerfile)
    if ($Dockerfile -notmatch '^Dockerfile(?:\.[a-z-]+)?$') { throw "Invalid job Dockerfile" }
    & git -C $Root diff --quiet --exit-code
    if ($LASTEXITCODE -ne 0) { throw "Commit tracked changes before deployment" }
    & git -C $Root diff --cached --quiet --exit-code
    if ($LASTEXITCODE -ne 0) { throw "Commit staged changes before deployment" }
    $sha = (& git -C $Root rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $sha -notmatch '^[0-9a-f]{40}$') { throw "A valid release commit is required" }
    $directory = Join-Path ([IO.Path]::GetTempPath()) ("chronos-job-release-" + [guid]::NewGuid().ToString('N'))
    $archive = "$directory.zip"
    & git -C $Root archive --format=zip --output $archive HEAD
    if ($LASTEXITCODE -ne 0) { throw "Unable to archive the release" }
    New-Item -ItemType Directory -Path $directory | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $directory
    $dockerSource = Join-Path $directory $Dockerfile
    if (-not (Test-Path -LiteralPath $dockerSource)) { throw "Job Dockerfile must be committed" }
    if ($Dockerfile -ne "Dockerfile") {
        Copy-Item -LiteralPath $dockerSource -Destination (Join-Path $directory "Dockerfile")
    }
    return @{ Directory = $directory; Archive = $archive; Sha = $sha }
}

function Remove-ChronosJobSource {
    param($Source)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $target = [IO.Path]::GetFullPath($Source.Directory)
    if (-not $target.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($target) -notmatch '^chronos-job-release-[a-f0-9]{32}$' -or
        [IO.Path]::GetFullPath($Source.Archive) -ne "$target.zip") {
        throw "Refusing to remove a path outside the job release directory"
    }
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Source.Archive -Force -ErrorAction SilentlyContinue
}
