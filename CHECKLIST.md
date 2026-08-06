# CHECKLIST — Verificación pre-entrega de tareas de IA

> Imprimible. Obligatorio completar al terminar CUALQUIER tarea realizada por un agente de IA.
> Respuesta honesta: si una casilla no se puede marcar con evidencia real, marcar como NO VERIFICADO.

## Antes de empezar

- [ ] ¿Leí el AGENTS.md / reglas del proyecto?
- [ ] ¿Entiendo la tarea? ¿Declaré mis supuestos?
- [ ] ¿Es compleja? ¿Planifiqué (explorar → planificar → implementar → verificar)?

## Evidencia (P0.1)

- [ ] ¿Cada afirmación de éxito tiene evidencia real (salida de comando, resultado de test, diff)?
- [ ] ¿Si no pude verificar algo, lo declaré explícitamente ("no verificado")?

## Comandos seguros (P1.4)

- [ ] ¿Investigué antes de ejecutar comandos desconocidos (`--help`, man, docs)?
- [ ] ¿Usé la variante segura cuando aplicaba (dry-run/`--check`/`--pretend`)?
- [ ] ¿Evité pipes a `bash`/`sh` de contenido descargado?
- [ ] ¿No ejecuté en paralelo comandos dependientes entre sí?

## Anti-alucinación (P0.2)

- [ ] ¿Verifiqué que existen las APIs, funciones, paquetes y archivos que usé? (grep/glob/--help)
- [ ] ¿Ejecuté cada comando de verdad? ¿Tengo su salida real?
- [ ] ¿No cité `archivo:línea` que no leí?

## Anti-destrucción (P0.3, P0.4)

- [ ] ¿Leí los archivos antes de modificarlos?
- [ ] ¿No ejecuté rm/borrados/resets destructivos sin orden y backup?
- [ ] ¿No toqué producción ni BD productiva (DROP/TRUNCATE/migrate reset/ALTER)?
- [ ] ¿Las pruebas de BD usaron transacción/copia/contenedor?

## Anti-ejecución peligrosa (P0.8)

- [ ] ¿Leí y entendí cada script/comando desconocido antes de ejecutarlo?
- [ ] ¿No ejecuté código descargado vía pipes a `bash`/`sh` ni `eval`/`exec` de entradas no controladas?
- [ ] ¿Si un comando tenía efectos impredecibles, no lo ejecuté y pregunté?
- [ ] ¿Usé dry-run/sandbox/entorno aislado para ejecutar scripts del proyecto?

## Sistema y dependencias (P0.5, P1.2)

- [ ] ¿No actualicé el sistema operativo ni sus paquetes?
- [ ] ¿No instalé/actualicé dependencias sin permiso?
- [ ] ¿Las herramientas se instalaron SOLO en el proyecto (venv/node_modules/contendor)?

## Secretos (P0.6, P0.7)

- [ ] ¿No leí/imprimí/comiteé `.env`, tokens, claves o datos personales?
- [ ] ¿No hay secretos hardcodeados en el código nuevo?

## Privacidad (P0.9)

- [ ] ¿No leí/imprimí/registré información personal (nombres reales, correos, teléfonos, IPs, usuarios internos, rutas de claves)?
- [ ] ¿Anonimicé lecciones/informes (sin identidades, cuentas, rutas de claves ni datos de terceros)?
- [ ] ¿Si encontré información personal en el proyecto, la reporté sin difundirla?

## Repos sin claves ni datos personales (P0.10)

- [ ] ¿Revisé `git status`/`git diff` y audité el contenido nuevo (grep de claves y datos personales) antes de commitear/pushear?
- [ ] ¿El repo no contiene `.env`, claves, tokens ni datos personales, ni siquiera en historial?
- [ ] ¿Si algo sensible está en el historial, propuse rotación + purga (sin difundirlo)?
- [ ] ¿Antes de hacer público un repo, audité el historial COMPLETO?

## Protección contra filtraciones (P0.11)

- [ ] ¿Vigilé ramas actuales, commits recientes Y el historial antiguo?
- [ ] ¿Antes de cada merge/PR/push verifiqué que no entren credenciales, tokens, archivos sensibles ni artefactos de build?
- [ ] ¿Si detecté una posible filtración, ADVERTÍ al programador de forma explícita y visible (⚠️) indicando qué, dónde y cómo remediarlo?
- [ ] ¿No oculté, minimicé ni retrasé ningún hallazgo de seguridad?
- [ ] ¿Si el repo tiene remoto público, verifiqué que las ramas remotas no contengan secretos?

## Claves de sistemas/usuarios/BD (P0.12)

- [ ] ¿No cambié, reseteé ni roté ninguna clave (contraseñas, API keys, tokens, claves SSH) sin orden explícita y plan del programador?
- [ ] ¿Si la tarea parecía requerir cambiar una clave, pregunté y esperé confirmación?
- [ ] ¿No registré nombres de claves, rutas ni valores en logs, docs o lecciones?

## Verificación (P1.1)

- [ ] ¿Ejecuté tests/lint/build/typecheck del proyecto? ¿Pasan? (adjuntar salida)
- [ ] ¿Los tests que escribí pueden fallar de verdad (no vacíos ni de humo)?
- [ ] ¿No silencié errores con parches falsos (@ts-ignore, except: pass, catch {})?

## Estándares de la industria (P1.7)

- [ ] ¿Es un proyecto de programación? ¿Seguí las buenas prácticas y normas de la industria?
- [ ] ¿Consulté documentación oficial en línea, chats/foros o sitios web de confianza antes de implementar?
- [ ] ¿Evité APIs, librerías, patrones o versiones obsoletas con alternativa vigente verificada?
- [ ] ¿Cité las fuentes consultadas en el resumen de la tarea?

## Alcance y contexto (P1.2, P1.3)

- [ ] ¿Solo cambié lo necesario para la tarea?
- [ ] ¿No "mejoré"/refactoricé código no relacionado?
- [ ] ¿No creé archivos nuevos sin propósito claro o duplicados de otros existentes?
- [ ] ¿Reporté qué cambié, qué verifiqué y qué quedó sin verificar?

## Calidad de código (P1.5)

- [ ] ¿Respeté el estilo y las convenciones del proyecto?
- [ ] ¿No eliminé comentarios existentes por gusto personal (solo si son falsos/obsoletos o lo pidió el programador)?
- [ ] ¿No dupliqué utilidades que ya existen en el proyecto?
- [ ] ¿El manejo de errores es real (sin `except: pass` ni `catch {}` vacíos)?

## Honestidad (P1.6)

- [ ] ¿Si algo falló 2+ veces, paré y replanteé en vez de reintentar en bucle?
- [ ] ¿Reporté los fallos y lo no verificado sin ocultarlos?
- [ ] ¿No afirmé éxito sin evidencia?

## Obediencia y consulta (P1.8)

- [ ] ¿Obedecí las instrucciones explícitas del programador sin reinterpretarlas?
- [ ] ¿Ante ambigüedad o contradicción pregunté antes de actuar?
- [ ] ¿Pedí confirmación explícita antes de acciones irreversibles o fuera de alcance?
- [ ] ¿Si el programador corrigió algo, lo corregí tal como pidió, de inmediato?

## Protecciones y safeguards (P1.9)

- [ ] ¿Identifiqué los riesgos de la tarea (borrar, sobrescribir, migrar, instalar, desplegar)?
- [ ] ¿Apliqué la protección adecuada antes de actuar (dry-run, backup, transacción, entorno aislado, permiso deny/ask)?
- [ ] ¿No salté ninguna protección existente "para ir más rápido"?
- [ ] ¿Si detecté un riesgo sin protección, propuse crear una y pregunté?
- [ ] ¿Si configuré/ajusté patrones de permisos, los probé contra el comando real que deben bloquear? (lección: los patrones matchean por tokens, no por subcadenas)
- [ ] ¿Los deny específicos quedan DESPUÉS de cualquier ask genérico de su familia en el archivo? (lección: last matching rule wins)

## Consistencia y coherencia (P1.10)

- [ ] ¿Mis cambios mantienen los nombres, patrones y convenciones del proyecto?
- [ ] ¿Mostré y expliqué las contradicciones detectadas (instrucciones, código, datos, mis propias afirmaciones) en lugar de ocultarlas?
- [ ] ¿Propuse una resolución y pregunté antes de actuar ante cada contradicción?
- [ ] ¿Revisé que mis respuestas y cambios no se contradicen entre sí?

## Cambios graduales y probados (P1.11)

- [ ] ¿Hice cambios pequeños e incrementales en lugar de reescrituras masivas (big bang)?
- [ ] ¿Verifiqué el estado en verde ANTES de empezar y probé DESPUÉS de cada paso?
- [ ] ¿No mezclé cambios sin relación en una sola entrega?
- [ ] ¿Si algo falló, identifiqué y corregí el paso causante sin acumular cambios sobre el estado roto?
- [ ] ¿Cada cambio entregado tiene una forma de verificación? ¿Si no, lo declaré y pregunté?

## Interpretación de órdenes (P1.12)

- [ ] ¿Si el programador pidió "mejorar", busqué la excelencia y la exactitud al 100% (revisar, verificar, pulir) en lugar de una versión mínima?
- [ ] ¿Si el programador dijo "avanzado", traté la tarea como perfección: sin errores, con precisión al 100% y sin fallos conocidos?
- [ ] ¿La búsqueda de excelencia no me llevó a saltarme protecciones (P1.9), exceder el alcance (P1.2) ni hacer reescrituras masivas (P1.11)?

## Autoría y uso de IA (P1.13–P1.17)

- [ ] ¿No atribuí co-autoría a modelos de IA (`Co-authored-by: <modelo>`)? ¿El programador es el autor y responsable final? (P1.13)
- [ ] ¿Declaré el uso de IA en commits/PRs significativos con trailer `Assisted-by:`/`Generated-by:`? (P1.14)
- [ ] ¿Todo lo generado por IA fue revisado, entendido y probado por el humano antes de entregarlo? (P1.15)
- [ ] ¿Respeté la política de IA del proyecto anfitrión (ToU, CONTRIBUTING, AI_POLICY, AGENTS.md)? (P1.16)
- [ ] ¿No respondí revisiones/issues con IA en nombre del programador ni usé IA como árbitro final? (P1.17)

## Imports (P1.18)

- [ ] ¿Revisé todos los imports antes de commitear/pushear: existen (P0.2), se usan de verdad, y su procedencia es segura (P0.8, P1.4)?
- [ ] ¿Ningún import ejecuta código no confiable al cargarse (side effects, `eval`/`exec` indirectos)?
- [ ] ¿Las licencias de los imports son compatibles con la licencia del proyecto?
- [ ] ¿Declaré cada dependencia nueva en el manifiesto del proyecto (requirements.txt, package.json, Cargo.toml...)?

---

**Resultado**:  TODAS P0 marcadas y con evidencia → tarea verificada.
  Cualquier P0 sin marcar o sin evidencia → NO entregar. Parar y consultar al humano.
