# Instancia unica compartida de Ollama para todos los proyectos con Vision IA 360.
#
# Recomendacion del paquete (ver README, seccion "Instancia unica de Ollama"):
#   - UNA sola instancia de Ollama en 127.0.0.1:11434 sirve a vision-360,
#     drilling-visualization y multistat (todos apuntan ya al mismo endpoint).
#   - Para optimizar la RAM local: OLLAMA_MAX_LOADED_MODELS=2 (solo el par
#     ligero qwen2.5vl:3b + gemma3:4b en memoria; gemma4:e2b se carga bajo
#     demanda) y OLLAMA_KEEP_ALIVE=30m (evita recargas a mitad de ejecucion;
#     default de Ollama: 5 min).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts/ollama_compartida.ps1          # solo estado
#   powershell -ExecutionPolicy Bypass -File scripts/ollama_compartida.ps1 -Apply   # aplica config + reinicia + limpia
#
# -Apply es idempotente: si las variables ya estan bien y la instancia corre,
# no reinicia nada. Nunca toca el sistema (solo variables de entorno de USUARIO
# y el proceso de la app de Ollama).
param([switch]$Apply)

$ErrorActionPreference = 'Stop'

$MAX = '2'
$KEEP = '30m'
$ollamaApp = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
$ollamaBin = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$PORT = 11434

function Write-Info($msg) { Write-Output "[ollama-compartida] $msg" }

Write-Info "== Estado de la instancia unica (puerto $PORT) =="

# 1. Binario y version
if (-not (Test-Path $ollamaBin)) { Write-Info "ERROR: no se encuentra ollama en $ollamaBin (instalalo primero)"; exit 1 }
$version = & $ollamaBin --version 2>&1 | Select-Object -First 1
Write-Info "binario: $version"

# 2. Proceso y puerto
$conn = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Info ("servidor escuchando en 127.0.0.1:{0} (PID {1})" -f $PORT, $conn.OwningProcess)
} else {
    Write-Info "SERVIDOR CAIDO: nada escucha en 127.0.0.1:$PORT"
}

# 3. Modelos cargados y RAM
$libre0 = $null
$os = Get-CimInstance Win32_OperatingSystem
$libre0 = $os.FreePhysicalMemory / 1MB
Write-Info ("RAM libre del host: {0:N1} GB" -f $libre0)
try {
    $ps = Invoke-RestMethod -Uri "http://127.0.0.1:$PORT/api/ps" -TimeoutSec 10
    if ($ps.models.Count -eq 0) { Write-Info 'modelos cargados: ninguno' }
    foreach ($m in $ps.models) {
        Write-Info ("modelo cargado: {0} ({1:N1} GB, hasta {2})" -f $m.name, ($m.size / 1GB), $m.expires_at)
    }
} catch {
    Write-Info "API no responde (la instancia no esta corriendo)."
}

# 4. Variables de entorno de usuario
$curMax = [System.Environment]::GetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', 'User')
$curKeep = [System.Environment]::GetEnvironmentVariable('OLLAMA_KEEP_ALIVE', 'User')
Write-Info ("envs de usuario: OLLAMA_MAX_LOADED_MODELS={0}  OLLAMA_KEEP_ALIVE={1}  (recomendado: {2} / {3})" -f $curMax, $curKeep, $MAX, $KEEP)

# 5. Huerfanos llama-server (padre muerto retiene RAM de modelos)
$huerfanos = @()
Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" | ForEach-Object {
    $parent = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $_.ParentProcessId) -ErrorAction SilentlyContinue
    if (-not $parent) { $huerfanos += $_ }
}
if ($huerfanos.Count -gt 0) {
    $ramH = ($huerfanos | Measure-Object WorkingSetSize -Sum).Sum / 1GB
    Write-Info ("ALERTA: {0} llama-server huerfano(s) retienen {1:N1} GB (padres muertos)" -f $huerfanos.Count, $ramH)
} else {
    Write-Info 'sin huerfanos llama-server'
}

if (-not $Apply) {
    Write-Info 'Solo estado (usa -Apply para aplicar la config recomendada, reiniciar y limpiar).'
    exit 0
}

# ---------- Aplicar config ----------
$cambio = ($curMax -ne $MAX) -or ($curKeep -ne $KEEP)

if ($cambio) {
    [System.Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', $MAX, 'User')
    [System.Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', $KEEP, 'User')
    Write-Info "envs de usuario actualizadas (OLLAMA_MAX_LOADED_MODELS=$MAX, OLLAMA_KEEP_ALIVE=$KEEP)."
    Write-Info 'Reiniciando la app de Ollama para que las tome (libera la RAM de los modelos)...'
    Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 3
    if (Test-Path $ollamaApp) { Start-Process $ollamaApp }
    Start-Sleep -Seconds 10
} elseif (-not $conn) {
    Write-Info 'envs correctas pero servidor caido: arrancando la app...'
    if (Test-Path $ollamaApp) { Start-Process $ollamaApp }
    Start-Sleep -Seconds 10
} else {
    Write-Info 'envs ya correctas y servidor arriba: no se reinicia nada.'
}

# Limpiar huerfanos (solo con padre muerto; safeguard PPID)
foreach ($h in $huerfanos) {
    Write-Info ("matando huerfano PID={0} ({1:N1} GB)" -f $h.ProcessId, ($h.WorkingSetSize / 1GB))
    Stop-Process -Id $h.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($huerfanos.Count -gt 0) { Start-Sleep -Seconds 3 }

# Verificacion final
$os = Get-CimInstance Win32_OperatingSystem
$libre1 = $os.FreePhysicalMemory / 1MB
Write-Info ("RAM libre tras aplicar: {0:N1} GB (antes: {1:N1} GB)" -f $libre1, $libre0)
$conn2 = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
if ($conn2) {
    Write-Info "OK: instancia unica corriendo en 127.0.0.1:$PORT (PID $($conn2.OwningProcess)). Modelos compartidos para todos los proyectos."
} else {
    Write-Info "ATENCION: la app no termino de levantar; espera unos segundos y re-ejecuta sin -Apply."
    exit 1
}
