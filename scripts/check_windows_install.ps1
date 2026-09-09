param([Parameter(Mandatory)][string]$Installer)
$ErrorActionPreference = 'Stop'

$installDirectory = Join-Path $env:RUNNER_TEMP 'echoroo-installed'
$installation = Start-Process -FilePath (Resolve-Path $Installer).Path -ArgumentList "/S /D=$installDirectory" -Wait -PassThru
if ($installation.ExitCode -ne 0) { throw "Installer exited with $($installation.ExitCode)" }

# Run the installed binary with an empty model directory, without downloading anything.
python "$PSScriptRoot/check_frozen.py" "$installDirectory/echoroo-sidecar.exe"
if ($LASTEXITCODE -ne 0) { throw 'Installed sidecar check failed' }

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing, System.Windows.Forms
$env:ECHOROO_DATA = Join-Path $env:RUNNER_TEMP 'echoroo-gui-data'

function Wait-ForDesktopText([string]$Text, [string]$EvidenceName) {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    $names = @()
    do {
        Start-Sleep -Milliseconds 500
        $application.Refresh()
        if ($application.HasExited) { throw 'Installed desktop application exited during startup' }
        if ($application.MainWindowHandle -ne 0) {
            $window = [System.Windows.Automation.AutomationElement]::FromHandle($application.MainWindowHandle)
            $elements = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
            $names = @($elements | ForEach-Object { $_.Current.Name })
        }
    } until ($names -contains $Text -or [DateTime]::UtcNow -ge $deadline)
    $names | Out-File (Join-Path $env:RUNNER_TEMP "$EvidenceName.txt") -Encoding utf8
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bitmap.Save((Join-Path $env:RUNNER_TEMP "$EvidenceName.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
    if ($names -notcontains $Text) { throw "The desktop UI did not display: $Text" }
    return $window
}

function Close-Desktop {
    if (-not $application.CloseMainWindow() -or -not $application.WaitForExit(30000)) {
        throw 'Installed desktop application did not close normally'
    }
}

$application = Start-Process "$installDirectory/echoroo-lite.exe" -PassThru
try {
    $window = Wait-ForDesktopText 'Local service connected' 'echoroo-startup'
    $language = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.AndCondition]::new(
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, 'Language'),
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::ComboBox)))
    if ($null -eq $language) { throw 'The language selector is not accessible' }
    $language.SetFocus()
    # Japanese is the first native select option; Home and Tab select and commit it.
    [System.Windows.Forms.SendKeys]::SendWait('{HOME}{TAB}')
    $window = Wait-ForDesktopText 'ローカルで接続済み' 'echoroo-startup-ja'
    Close-Desktop

    $application = Start-Process "$installDirectory/echoroo-lite.exe" -PassThru
    $window = Wait-ForDesktopText 'ローカルで接続済み' 'echoroo-startup-ja-restarted'
    Close-Desktop
    Write-Output 'NSIS installation, installed sidecar API/quiz, English/Japanese desktop connection, locale persistence and shutdown passed.'
} finally {
    if (-not $application.HasExited) { Stop-Process -Id $application.Id -Force }
}
