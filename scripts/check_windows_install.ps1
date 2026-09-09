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

function Press-DesktopButton([string]$Name) {
    $element = Find-DesktopControl $Name ([System.Windows.Automation.ControlType]::Button)
    $element.SetFocus()
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
}

function Select-FirstSaved([string]$Name) {
    $element = Find-DesktopControl $Name ([System.Windows.Automation.ControlType]::ComboBox)
    $element.SetFocus()
    [System.Windows.Forms.SendKeys]::SendWait('{HOME}{DOWN}{TAB}')
}

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
    $language = Find-DesktopControl 'Language' ([System.Windows.Automation.ControlType]::ComboBox)
    $language.SetFocus()
    # Japanese is the first native select option; Home and Tab select and commit it.
    [System.Windows.Forms.SendKeys]::SendWait('{HOME}{TAB}')
    $window = Wait-ForDesktopText 'ローカルで接続済み' 'echoroo-startup-ja'
    Close-Desktop

    $application = Start-Process "$installDirectory/echoroo-lite.exe" -PassThru
    $window = Wait-ForDesktopText 'ローカルで接続済み' 'echoroo-startup-ja-restarted'
    if ($Data) {
        $window = Wait-ForDesktopText 'Perch V2' 'echoroo-startup-results'
        $null = Find-DesktopControl 'BirdNET 2.4' ([System.Windows.Automation.ControlType]::Text)
        Select-FirstSaved '検索履歴'
        $window = Wait-ForDesktopText '類似度の分布と判定' 'echoroo-startup-search'
        Select-FirstSaved '判定する区間'
        $window = Wait-ForDesktopText '対象種 対象外 不明 保存済み' 'echoroo-startup-judgements'

        Press-DesktopButton '報告'
        $window = Wait-ForDesktopText '報告の履歴' 'echoroo-startup-report-history'
        Select-FirstSaved '報告の履歴'
        $window = Wait-ForDesktopText 'Spiral plot' 'echoroo-startup-report'
        $plot = Find-DesktopControl 'Spiral plot' ([System.Windows.Automation.ControlType]::Group)
        $cells = $plot.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button))
        if ($cells.Count -ne 24) { throw "Expected 24 hourly plot cells, got $($cells.Count)" }
        $cell = @($cells | Where-Object { $_.Current.Name -like '2024-08-08 6:00*' })[0]
        $cell.SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        $window = Wait-ForDesktopText '2024-08-08 · 6:00 · 解析済み' 'echoroo-startup-report-cell'

        Press-DesktopButton '鳴き声クイズ'
        $window = Wait-ForDesktopText '学習を始める' 'echoroo-startup-quiz'
        Press-DesktopButton '学習を始める'
        $window = Wait-ForDesktopText '音源を聴いて、対象の種を選んでください。' 'echoroo-startup-quiz-question'
        Press-DesktopButton 'わからない'
        $window = Wait-ForDesktopText '次の問題' 'echoroo-startup-quiz-feedback'
        Write-Output 'Installed Japanese desktop restored both model results, saved search/judgement, 24-cell spiral report and quiz feedback.'
    }
    Close-Desktop
    Write-Output 'NSIS installation, installed sidecar API/quiz, English/Japanese desktop connection, locale persistence and shutdown passed.'
} finally {
    if (-not $application.HasExited) { Stop-Process -Id $application.Id -Force }
}
