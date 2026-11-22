<#
.SYNOPSIS
    Uruchamia system COI jednym poleceniem: buduje obrazy, startuje usługi z panelem urzędnika,
    czeka na gotowość i wystawia token JWT do panelu.

.DESCRIPTION
    Pierwsze uruchomienie tworzy .env z .env.example z losowymi sekretami (JWT_SECRET_KEY,
    POSTGRES_PASSWORD) i pyta o klucz Eden AI (bez klucza: tryb demonstracyjny). Istniejącego
    .env skrypt nie zmienia. Pierwsze budowanie obrazów trwa kilkanaście minut (~5 GB).

    Na Windows uruchamiaj przez start.cmd (działa też dwuklikiem) — omija blokadę skryptów
    PowerShell (domyślna polityka Restricted).

.EXAMPLE
    .\start.cmd                 # start i otwarcie panelu w przeglądarce
    .\start.cmd -Stop           # zatrzymanie (dane w bazie zostają)
    .\start.cmd -Token          # nowy token JWT (np. po wygaśnięciu)
    .\start.cmd -Logs           # logi API i workera na żywo (Ctrl+C kończy)
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Token,
    [switch]$Logs,
    [switch]$NoBrowser,
    [string]$User = 'urzednik',
    [ValidateRange(1, 1440)][int]$TokenMinutes = 480
)

Set-Location -LiteralPath $PSScriptRoot
$EnvFile = Join-Path $PSScriptRoot '.env'

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

# start.cmd uruchomiony dwuklikiem (Eksplorator → cmd → powershell): okno zamknęłoby się od razu.
function Wait-IfDoubleClicked {
    try {
        $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
        $grandparent = (Get-CimInstance Win32_Process -Filter "ProcessId=$parent").ParentProcessId
        if ((Get-Process -Id $grandparent -ErrorAction Stop).ProcessName -eq 'explorer') {
            Read-Host 'Naciśnij Enter, aby zamknąć okno' | Out-Null
        }
    } catch { }
}

function Exit-WithError([string]$Text) {
    Write-Host "BŁĄD: $Text" -ForegroundColor Red
    Wait-IfDoubleClicked
    exit 1
}

function Invoke-Compose {
    & docker compose --profile frontend @args
    if ($LASTEXITCODE -ne 0) { Exit-WithError "docker compose $($args -join ' ') — kod $LASTEXITCODE" }
}

function Get-EnvValue([string]$Name, [string]$Default = '') {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return $Default }
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line -match "^\s*$Name\s*=\s*(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $Default
}

function New-Secret {
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    # base64url bez dopełnienia: bezpieczne w .env i w adresie bazy (DATABASE_URL)
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Read-EdenAiKey {
    if ($env:EDENAI_API_KEY) { return $env:EDENAI_API_KEY.Trim() }
    # Bez konsoli (np. CI) Read-Host czekałby w nieskończoność — wtedy bez klucza.
    if ([Console]::IsInputRedirected) { return '' }
    try {
        $secure = Read-Host 'Klucz API Eden AI (Enter = tryb demonstracyjny bez modelu)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    } catch {
        return ''  # sesja bez konsoli — bez klucza
    }
}

function New-EnvFile {
    Write-Step 'Tworzę .env z .env.example (losowe sekrety)'
    $key = Read-EdenAiKey
    $lines = foreach ($line in [System.IO.File]::ReadAllLines((Join-Path $PSScriptRoot '.env.example'))) {
        if ($line -match '^JWT_SECRET_KEY=') { "JWT_SECRET_KEY=$(New-Secret)" }
        elseif ($line -match '^POSTGRES_PASSWORD=') { "POSTGRES_PASSWORD=$(New-Secret)" }
        elseif ($line -match '^EDENAI_API_KEY=') { "EDENAI_API_KEY=$key" }
        elseif ($line -match '^LLM_DEMO_MODE=' -and -not $key) { 'LLM_DEMO_MODE=true' }
        else { $line }
    }
    # UTF-8 bez BOM, końce linii LF — tak jak czyta je docker compose
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), $utf8)
    if (-not $key) {
        Write-Host 'Brak klucza Eden AI: LLM_DEMO_MODE=true — wyniki demonstracyjne, nie do importu.' -ForegroundColor Yellow
        Write-Host 'Aby użyć modelu: wpisz EDENAI_API_KEY w .env, ustaw LLM_DEMO_MODE=false i uruchom ponownie.' -ForegroundColor Yellow
    }
}

function Assert-Docker {
    & docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) { Exit-WithError 'Docker nie działa — uruchom Docker Desktop i spróbuj ponownie.' }
}

function Show-Token {
    $jwt = (& docker compose exec -T api python -m backend.api.token_cli --sub $User --minutes $TokenMinutes | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $jwt) {
        Write-Host 'Nie wystawiono tokenu (JWT wyłączony albo API nie działa — sprawdź: .\start.cmd -Logs).' -ForegroundColor Yellow
        return
    }
    $hours = [math]::Round($TokenMinutes / 60, 1)
    try {
        Set-Clipboard -Value $jwt
        Write-Host "Token JWT dla '$User' (ważny $hours h) jest w schowku — w panelu kliknij "Token API" i wklej:" -ForegroundColor Green
    } catch {
        Write-Host "Token JWT dla '$User' (ważny $hours h) — w panelu kliknij "Token API" i wklej:" -ForegroundColor Green
    }
    Write-Host $jwt
}

if ($Stop) {
    Write-Step 'Zatrzymuję usługi (dane w bazie zostają)'
    Invoke-Compose down
    exit 0
}
if ($Logs) {
    & docker compose --profile frontend logs -f --tail 100 api worker
    exit 0
}
if ($Token) {
    Show-Token
    exit 0
}

if (-not (Test-Path -LiteralPath $EnvFile)) { New-EnvFile }
if (-not (Get-EnvValue 'EDENAI_API_KEY') -and (Get-EnvValue 'LLM_DEMO_MODE' 'false') -ne 'true') {
    Write-Host 'Uwaga: .env nie ma EDENAI_API_KEY, a LLM_DEMO_MODE=false — analizy zakończą się błędem.' -ForegroundColor Yellow
}
Assert-Docker

Write-Step 'Buduję obrazy i uruchamiam usługi (pierwszy raz: kilkanaście minut)'
Invoke-Compose up -d --build --wait --wait-timeout 900

$frontendUrl = "http://localhost:$(Get-EnvValue 'FRONTEND_PORT' '8080')"
$apiUrl = "http://localhost:$(Get-EnvValue 'API_PORT' '8000')"
Write-Host ''
Write-Host "Panel urzędnika:  $frontendUrl" -ForegroundColor Green
Write-Host "API (OpenAPI):    $apiUrl/api/docs" -ForegroundColor Green
Write-Host ''
Show-Token
Write-Host ''
Write-Host 'Zatrzymanie: .\start.cmd -Stop   Nowy token: .\start.cmd -Token   Logi: .\start.cmd -Logs'
if (-not $NoBrowser) { Start-Process $frontendUrl }
Wait-IfDoubleClicked
