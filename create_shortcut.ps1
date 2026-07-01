$ErrorActionPreference = "Stop"

function FromUtf8Base64($value) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value))
}

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exeName = FromUtf8Base64 "5paH5qGI5Lit5p6iLmV4ZQ=="
$launcherName = FromUtf8Base64 "5ZCv5Yqo5paH5qGI5bCP56iL5bqPLmJhdA=="
$desktopFallback = FromUtf8Base64 "RDpc5qGM6Z2i"
$shortcutName = FromUtf8Base64 "5paH5qGI5bCP56iL5bqPLmxuaw=="

$exe = Join-Path $appDir $exeName
$launcher = Join-Path $appDir $launcherName
$icon = Join-Path $appDir "app.ico"

if (Test-Path $exe) {
    $target = $exe
} elseif (Test-Path $launcher) {
    $target = $launcher
} else {
    throw "Main program or launcher was not found."
}

$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop -or -not (Test-Path $desktop)) {
    $desktop = $desktopFallback
}
if (-not (Test-Path $desktop)) {
    New-Item -ItemType Directory -Path $desktop -Force | Out-Null
}

$shortcut = Join-Path $desktop $shortcutName
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $target
$link.Arguments = ""
$link.WorkingDirectory = $appDir
if (Test-Path $icon) {
    $link.IconLocation = $icon
}
$link.Description = "Wenan App"
$link.Save()

Write-Host ("Shortcut created: " + $shortcut)
