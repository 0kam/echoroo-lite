param([Parameter(Mandatory)][string]$Installer, [string]$Data)
$ErrorActionPreference = 'Stop'

$installDirectory = Join-Path $env:RUNNER_TEMP 'echoroo-installed'
$installation = Start-Process -FilePath (Resolve-Path $Installer).Path -ArgumentList "/S /D=$installDirectory" -Wait -PassThru
if ($installation.ExitCode -ne 0) { throw "Installer exited with $($installation.ExitCode)" }

# Run the installed binary with an empty model directory, without downloading anything.
python "$PSScriptRoot/check_frozen.py" "$installDirectory/echoroo-sidecar.exe"
if ($LASTEXITCODE -ne 0) { throw 'Installed sidecar check failed' }

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing, System.Windows.Forms
$env:ECHOROO_DATA = if ($Data) { (Resolve-Path $Data).Path } else { Join-Path $env:RUNNER_TEMP 'echoroo-gui-data' }

function Find-DesktopControl([string]$Name, [System.Windows.Automation.ControlType]$Type) {
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($application.MainWindowHandle)
    $element = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.AndCondition]::new(
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, $Name),
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $Type)))
    if ($null -eq $element) { throw "The desktop control is not accessible: $Name" }
    return $element
}

function Find-DesktopAction([string]$Name) {
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($application.MainWindowHandle)
    foreach ($type in @([System.Windows.Automation.ControlType]::Hyperlink, [System.Windows.Automation.ControlType]::Button)) {
        $element = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.AndCondition]::new(
                [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, $Name),
                [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $type)))
        if ($null -ne $element) { return $element }
    }
    throw "The desktop action is not accessible: $Name"
}

function Invoke-DesktopElement($element) {
    # WebView2 exposes links and buttons with the Invoke pattern; focus and Enter is the fallback.
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        $pattern.Invoke()
        return
    }
    $element.SetFocus()
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
}

function Press-DesktopAction([string]$Name) {
    Invoke-DesktopElement (Find-DesktopAction $Name)
}

function Press-FirstTableAction {
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($application.MainWindowHandle)
    $table = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Table))
    if ($null -eq $table) { throw 'The desktop UI has no accessible table' }
    foreach ($type in @([System.Windows.Automation.ControlType]::Hyperlink, [System.Windows.Automation.ControlType]::Button)) {
        $element = $table.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $type))
        if ($null -ne $element) {
            Invoke-DesktopElement $element
            return
        }
    }
    throw 'The accessible table has no action'
}

function Wait-ForDesktopText([string]$Text, [string]$EvidenceName, [int]$MinimumCount = 1) {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    $names = @()
    $matches = 0
    do {
        Start-Sleep -Milliseconds 500
        $application.Refresh()
        if ($application.HasExited) { throw 'Installed desktop application exited during startup' }
        if ($application.MainWindowHandle -ne 0) {
            $window = [System.Windows.Automation.AutomationElement]::FromHandle($application.MainWindowHandle)
            $elements = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
            $names = @($elements | ForEach-Object { $_.Current.Name })
        }
        # Links may carry a decorative arrow in their accessible name (for example '‹ 種の一覧に戻る').
        $matches = @($names | Where-Object { ($_ -replace '^[‹›]\s*|\s*[‹›]$', '') -eq $Text }).Count
    } until ($matches -ge $MinimumCount -or [DateTime]::UtcNow -ge $deadline)
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
    if ($matches -lt $MinimumCount) {
        # Artifacts may be unavailable, so the log must show what the window offered instead.
        Write-Host "Accessible names on screen:"; $names | Where-Object { $_ } | Select-Object -First 150 | ForEach-Object { Write-Host "  $_" }
        throw "The desktop UI did not display: $Text"
    }
    return $window
}

function Close-Desktop {
    if (-not $application.CloseMainWindow() -or -not $application.WaitForExit(30000)) {
        throw 'Installed desktop application did not close normally'
    }
}

$application = Start-Process "$installDirectory/echoroo-lite.exe" -PassThru
try {
    # The navigation is visible before connection; its matching page heading is the second occurrence.
    $window = Wait-ForDesktopText 'Recording sites' 'echoroo-startup' 2
    $language = Find-DesktopControl 'Language' ([System.Windows.Automation.ControlType]::ComboBox)
    $language.SetFocus()
    # Japanese is the first native select option; Home and Tab select and commit it.
    [System.Windows.Forms.SendKeys]::SendWait('{HOME}{TAB}')
    $window = Wait-ForDesktopText '録音した地点' 'echoroo-startup-ja' 2
    Close-Desktop

    $application = Start-Process "$installDirectory/echoroo-lite.exe" -PassThru
    $window = Wait-ForDesktopText '録音した地点' 'echoroo-startup-ja-restarted' 2
    if ($Data) {
        $window = Wait-ForDesktopText 'Test' 'echoroo-startup-site'
        Press-DesktopAction '結果を見る'
        $window = Wait-ForDesktopText '調べている種' 'echoroo-startup-species-list'
        Press-FirstTableAction
        $window = Wait-ForDesktopText '種の一覧に戻る' 'echoroo-startup-species'
        $window = Wait-ForDesktopText 'いるか' 'echoroo-startup-species-presence'
        $window = Wait-ForDesktopText 'いつ・どれくらいか' 'echoroo-startup-species-counting'

        Press-DesktopAction '鳴き声クイズ'
        $window = Wait-ForDesktopText 'クイズに使う鳴き声を追加' 'echoroo-startup-quiz'
        Write-Output 'Installed Japanese desktop opened the current site, species and empty quiz page.'
    }
    Close-Desktop
    Write-Output 'NSIS installation, installed sidecar API, English/Japanese site list, locale persistence and shutdown passed.'
} finally {
    if (-not $application.HasExited) { Stop-Process -Id $application.Id -Force }
}
