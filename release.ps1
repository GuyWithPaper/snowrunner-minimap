# Build the installer and (optionally) publish it as a GitHub release so installed copies auto-update.
#   .\release.ps1 1.0.1            build only  -> dist\SnowRunnerMinimap-Setup.exe
#   .\release.ps1 1.0.1 -Publish   build + push a GitHub release (needs `gh auth login`)
param([Parameter(Mandatory = $true)][string]$Version, [switch]$Publish, [string]$Notes = "")
$ErrorActionPreference = "Continue"   # native tools log to stderr; we check $LASTEXITCODE instead
Set-Location $PSScriptRoot
$repo = (Select-String -Path minimap\version.py -Pattern 'REPO = "(.+)"').Matches[0].Groups[1].Value
Set-Content -Path minimap\version.py -Encoding utf8 -Value "VERSION = `"$Version`"`nREPO = `"$repo`""
$parts = ($Version.Split('.') + @('0','0','0'))[0..3] -join ', '
@"
VSVersionInfo(ffi=FixedFileInfo(filevers=($parts), prodvers=($parts)),
  kids=[StringFileInfo([StringTable('040904B0', [StringStruct('ProductName', 'SnowRunner Minimap'),
  StringStruct('FileDescription', 'SnowRunner Minimap'), StringStruct('FileVersion', '$Version'),
  StringStruct('ProductVersion', '$Version')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"@ | Set-Content -Encoding utf8 installer\file_version.txt
python -m PyInstaller --log-level WARN --noconfirm --clean --workpath build\pyi\work --distpath build\pyi\dist installer\SnowRunnerMinimap.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
& $iscc "/DAppVersion=$Version" installer\SnowRunnerMinimap.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "Built dist\SnowRunnerMinimap-Setup.exe ($Version)"
if ($Publish) {
  git add -A; git commit -m "Release v$Version" --allow-empty; git push
  if (-not $Notes) { $Notes = "SnowRunner Minimap v$Version" }
  gh release create "v$Version" dist\SnowRunnerMinimap-Setup.exe --repo $repo --title "v$Version" --notes $Notes
  Write-Host "Published v$Version - installed copies will update within a few hours (or on next start)."
}
