param([ValidateSet("listen","speak")][string]$Mode)
$ErrorActionPreference = "Stop"
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
if ($Mode -eq "speak") {
    $voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try { $voice.Speak([Console]::In.ReadToEnd()) } finally { $voice.Dispose() }
} else {
    $info = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
        Where-Object { $_.Culture.TwoLetterISOLanguageName -eq "zh" } | Select-Object -First 1
    if (-not $info) { throw "Local Chinese recognizer unavailable" }
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($info)
    try {
        $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
        $engine.SetInputToDefaultAudioDevice()
        $heard = $engine.Recognize([TimeSpan]::FromSeconds(8))
        if ($heard) { [Console]::Write($heard.Text) }
    } finally { $engine.Dispose() }
}
