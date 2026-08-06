# Servicio UNICO del daemon OCR + Vision IA + Chart de vision-360.
#
# Objetivo: UNA sola instancia de ocr_server.py (servicio unificado con
# /ocr, /ask, /chart y /vision) en el puerto canonico 127.0.0.1:8131,
# compartida por todos los proyectos locales (drilling-visualization,
# multistat, vision-360...). Arranca al iniciar sesion (tarea programada
# ONLOGON, sin privilegios de administrador) y NO se auto-cierra por
# inactividad (--timeout 0 = servicio permanente).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-vision.ps1 -Instalar
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-vision.ps1 -Iniciar
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-vision.ps1 -Detener
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-vision.ps1 -Estado
#
# Sin argumentos: muestra el estado (equivalente a -Estado).

param(
    [switch]$Instalar,
    [switch]$Iniciar,
    [switch]$Detener,
    [switch]$Estado
)

$ErrorActionPreference = 'Stop'
$TAREA = 'Vision360-Ocr'
$PUERTO = 8131
$URL = "http://127.0.0.1:$PUERTO/health"
$RAIZ = Split-Path -Parent $PSScriptRoot
$PY = Join-Path $RAIZ '.venv-ocr\Scripts\python.exe'
$DAEMON = Join-Path $RAIZ 'ocr_server.py'

function Test-ServicioVivo {
    try {
        $r = Invoke-WebRequest -Uri $URL -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Get-ProcesoDaemon {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*ocr_server.py*" -and $_.CommandLine -like "*$PUERTO*" }
}

function Write-Estado {
    $tarea = Get-ScheduledTask -TaskName $TAREA -ErrorAction SilentlyContinue
    $vivo = Test-ServicioVivo
    $procs = Get-ProcesoDaemon
    Write-Host "Autostart tarea : $TAREA -> $([bool]$tarea)"
    Write-Host "Puerto $PUERTO : $vivo"
    Write-Host "Procesos daemon: $($procs.Count) (PIDs: $($procs.ProcessId -join ', '))"
    if ($tarea) {
        Write-Host "Estado tarea    : $($tarea.State)"
        $info = Get-ScheduledTaskInfo -TaskName $TAREA
        Write-Host "Ultima ejecucion: $($info.LastRunTime) (resultado $($info.LastTaskResult))"
    }
    if ($vivo) {
        try {
            $r = Invoke-WebRequest -Uri $URL -TimeoutSec 3 -UseBasicParsing
            Write-Host "Health          : $($r.Content)"
        } catch { Write-Host "Health          : no responde" }
    }
}

if (-not ($Instalar -or $Iniciar -or $Detener -or $Estado)) {
    $Estado = $true
}

if ($Instalar) {
    if (-not (Test-Path $PY)) { throw "No hay venv OCR en $PY (ejecuta setup-ocr.ps1 antes)." }
    if (-not (Test-Path $DAEMON)) { throw "No se encuentra el daemon en $DAEMON." }
    try {
        Write-Host "Intentando tarea programada '$TAREA' (requiere admin)..."
        $accion = New-ScheduledTaskAction -Execute $PY -Argument "`"$DAEMON`" --port $PUERTO --timeout 0 --ask-engine ollama" -WorkingDirectory $RAIZ
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TAREA -Action $accion -Trigger $trigger `
            -Settings $settings -Description "Servicio unico OCR + Vision IA + Chart (vision-360, puerto $PUERTO)" | Out-Null
        Write-Host "Tarea creada. El daemon arranca al iniciar sesion (puerto $PUERTO)."
    } catch {
        Write-Host "Sin permisos para tarea programada ($($_.Exception.Message))."
        Write-Host "Alternativa manual: inicia el daemon con scripts/servicio-vision.ps1 -Iniciar"
        Write-Host "o con un acceso directo en la carpeta Inicio apuntando a:"
        Write-Host "  $PY `"$DAEMON`" --port $PUERTO --timeout 0"
    }
    Write-Host "Para arrancarla ahora: scripts/servicio-vision.ps1 -Iniciar"
}

if ($Iniciar) {
    if (Test-ServicioVivo) {
        Write-Host "El servicio ya responde en $URL. Nada que hacer."
    } else {
        if (-not (Test-Path $PY)) { throw "No hay venv OCR en $PY (ejecuta setup-ocr.ps1 antes)." }
        Write-Host "Arrancando daemon unico (puerto $PUERTO, --timeout 0)..."
        Start-Process -FilePath $PY -ArgumentList "`"$DAEMON`"", "--port", "$PUERTO", "--timeout", "0", "--ask-engine", "ollama" -WorkingDirectory $RAIZ -WindowStyle Hidden
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            if (Test-ServicioVivo) { break }
        }
        if (Test-ServicioVivo) {
            Write-Host "Servicio respondiendo en $URL"
        } else {
            Write-Host "AVISO: el servicio aun no responde tras 30s. Revisa el proceso."
        }
    }
}

if ($Detener) {
    $procs = Get-ProcesoDaemon
    if (-not $procs) {
        Write-Host "No hay procesos del daemon en el puerto $PUERTO."
    } else {
        foreach ($p in $procs) {
            Write-Host "Deteniendo PID $($p.ProcessId) ..."
            Stop-Process -Id $p.ProcessId -Force
        }
        Start-Sleep -Seconds 2
        if (Test-ServicioVivo) {
            Write-Host "AVISO: el puerto $PUERTO sigue respondiendo (otra instancia?)."
        } else {
            Write-Host "Daemon detenido. Puerto $PUERTO libre."
        }
    }
}

if ($Estado) { Write-Estado }
