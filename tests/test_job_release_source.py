import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_job_source_uses_committed_files_and_checks_cleanup_boundary(tmp_path):
    shell = shutil.which('pwsh')
    if shell is None:
        pytest.skip('PowerShell is unavailable on this platform')
    repository = tmp_path / 'repo'
    repository.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repository), *args], capture_output=True, check=True)
    git('init', '-q')
    git('config', 'user.name', 'Test Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'core.hooksPath', str(repository / '.no-hooks'))
    (repository / 'Dockerfile.assignment-scan').write_text('FROM python:3.12-slim\n', encoding='utf-8')
    (repository / 'module.py').write_text('COMMITTED = True\n', encoding='utf-8')
    git('add', 'Dockerfile.assignment-scan', 'module.py')
    git('commit', '-qm', 'Create the release test fixture')
    (repository / '.env').write_text('SYNTHETIC_SECRET=must-not-ship', encoding='utf-8')
    helper = Path(__file__).resolve().parents[1] / 'scripts/cloud_job_release.ps1'
    script = tmp_path / 'check.ps1'
    script.write_text(f'''
$ErrorActionPreference = 'Stop'
. '{helper.as_posix()}'
$source = New-ChronosJobSource -Root '{repository.as_posix()}' -Dockerfile 'Dockerfile.assignment-scan'
try {{
    if (Test-Path -LiteralPath (Join-Path $source.Directory '.env')) {{ throw 'Untracked secret was shipped' }}
    if ((Get-Content (Join-Path $source.Directory 'module.py') -Raw).Trim() -ne 'COMMITTED = True') {{ throw 'Wrong source' }}
    if ((Get-Content (Join-Path $source.Directory 'Dockerfile') -Raw).Trim() -ne 'FROM python:3.12-slim') {{ throw 'Wrong Dockerfile' }}
}} finally {{ Remove-ChronosJobSource $source }}
if (Test-Path -LiteralPath $source.Directory) {{ throw 'Release source was not removed' }}
try {{
    Remove-ChronosJobSource @{{ Directory = '{repository.as_posix()}'; Archive = '{repository.as_posix()}.zip' }}
    throw 'Cleanup escaped its boundary'
}} catch {{
    if ($_.Exception.Message -ne 'Refusing to remove a path outside the job release directory') {{ throw }}
}}
Write-Output '{{"committed_source":true,"cleanup_boundary":true}}'
''', encoding='utf-8')
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'committed_source': True, 'cleanup_boundary': True}
