# AI: model=deepseek-v4-flash date=2026-08-06 task=servicio-unico-ollama
# Servicio UNICO de Ollama para todos los proyectos con Vision IA 360.
#
# Objetivo: UNA sola instancia de `ollama serve` en el puerto canonico
# 127.0.0.1:11434, arrancando al iniciar sesion (tarea programada
# ONLOGON, sin privilegios de administrador) y compartida por todos los
# proyectos locales (multistat, drilling-visualization, vision-360...).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-ollama.ps1 -Instalar
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-ollama.ps1 -Iniciar
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-ollama.ps1 -Detener
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-ollama.ps1 -Estado
#   powershell -ExecutionPolicy Bypass -File scripts/servicio-ollama.ps1 -Verificar
#
# Sin argumentos: muestra el estado (equivalente a -Estado).

param(
    [switch]$Instalar,
    [switch]$Iniciar,
    [switch]$Detener,
    [switch]$Estado,
    [switch]$Verificar
)

$ErrorActionPreference = 'Stop'
$TAREA = 'Vision360-Ollama'
$PUERTO = 11434
$URL = "http://127.0.0.1:$PUERTO/api/version"
$RUN_KEY = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RUN_VAL = 'Vision360-Ollama'

function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cand = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "C:\Program Files\Ollama\ollama.exe"
    )
    foreach ($c in $cand) { if (Test-Path $c) { return $c } }
    throw 'ollama no encontrado. Instala Ollama (https://ollama.com/download).'
}

function Test-OllamaVivo {
    try {
        $r = Invoke-WebRequest -Uri $URL -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Get-OllamaVersion {
    try {
        $r = Invoke-WebRequest -Uri $URL -TimeoutSec 3 -UseBasicParsing
        return ($r.Content | ConvertFrom-Json).version
    } catch { return $null }
}

function Get-ProcesoServe {
    Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" |
        Where-Object { $_.CommandLine -like '*serve*' }
}

function Write-Estado {
    $tarea = Get-ScheduledTask -TaskName $TAREA -ErrorAction SilentlyContinue
    $run = Get-ItemProperty -Path $RUN_KEY -Name $RUN_VAL -ErrorAction SilentlyContinue
    $vivo = Test-OllamaVivo
    $ver = Get-OllamaVersion
    $procs = Get-ProcesoServe
    Write-Host "Autostart tarea   : $TAREA -> $([bool]$tarea)"
    Write-Host "Autostart HKCU Run: $([bool]$run)"
    Write-Host "Puerto $PUERTO   : $vivo $(if ($ver) { "(ollama $ver)" })"
    Write-Host "Procesos serve  : $($procs.Count) (PIDs: $($procs.ProcessId -join ', '))"
    if ($tarea) {
        Write-Host "Estado tarea     : $($tarea.State)"
        $info = Get-ScheduledTaskInfo -TaskName $TAREA
        Write-Host "Ultima ejecucion : $($info.LastRunTime) (resultado $($info.LastTaskResult))"
    }
}

if (-not ($Instalar -or $Iniciar -or $Detener -or $Estado -or $Verificar)) {
    $Estado = $true
}

if ($Instalar) {
    $ollama = Find-Ollama
    # 1) Tarea programada ONLOGON (requiere admin para registrarla)
    try {
        Write-Host "Intentando tarea programada '$TAREA' (requiere admin)..."
        $accion = New-ScheduledTaskAction -Execute $ollama -Argument 'serve'
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TAREA -Action $accion -Trigger $trigger `
            -Settings $settings -Description "Ollama serve unico (Vision IA 360, puerto $PUERTO)" | Out-Null
        Write-Host "Tarea creada. Ollama arranca al iniciar sesion."
    } catch {
        Write-Host "Sin permisos para tarea programada ($($_.Exception.Message))."
        # 2) Fallback sin admin: autostart HKCU Run
        Write-Host "Usando autostart HKCU Run en su lugar..."
        Set-ItemProperty -Path $RUN_KEY -Name $RUN_VAL -Type String `
            -Value "`"$ollama`" serve"
        Write-Host "Autostart HKCU Run creado ($RUN_VAL). Ollama arranca al iniciar sesion."
    }
    Write-Host "Para arrancarla ahora: scripts/servicio-ollama.ps1 -Iniciar"
}

if ($Iniciar) {
    if (Test-OllamaVivo) {
        Write-Host "ollama ya responde en $URL (version $(Get-OllamaVersion)). Nada que hacer."
    } else {
        $ollama = Find-Ollama
        Write-Host "Arrancando $ollama serve (puerto $PUERTO)..."
        Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 1
            if (Test-OllamaVivo) { break }
        }
        if (Test-OllamaVivo) {
            Write-Host "ollama responde en $URL (version $(Get-OllamaVersion))"
        } else {
            Write-Host "AVISO: ollama aun no responde tras 15s. Revisa el proceso."
        }
    }
}

if ($Detener) {
    $procs = Get-ProcesoServe
    if (-not $procs) {
        Write-Host "No hay procesos ollama.exe serve corriendo."
    } else {
        foreach ($p in $procs) {
            Write-Host "Deteniendo PID $($p.ProcessId) ..."
            Stop-Process -Id $p.ProcessId -Force
        }
        Start-Sleep -Seconds 2
        if (Test-OllamaVivo) {
            Write-Host "AVISO: el puerto $PUERTO sigue respondiendo."
        } else {
            Write-Host "ollama detenido. Puerto $PUERTO libre."
        }
    }
}

if ($Estado) { Write-Estado }

if ($Verificar) {
    Write-Estado
    Write-Host ""
    Write-Host "Modelos disponibles:"
    ollama list 2>$null
    Write-Host ""
    Write-Host "Los proyectos consumen el MISMO servidor en $URL"
    Write-Host "  - vision-360/ocr_server.py      (POST /ask engine=ollama|gemma3)"
    Write-Host "  - multistat/descripcion_360.py  (VLM por API HTTP)"
    Write-Host "  - drilling-visualization/vision360.py (--engine ollama)"
}
