# TIC Live V2.5 — activación y sincronización de velas

## Problemas corregidos

1. El monitor ya no trata la última vela que devuelve Bitunix como si necesariamente fuera la última vela cerrada. Solo admite entradas cuando el timestamp de apertura de la vela coincide con el último cierre teórico de la temporalidad. Si la API va retrasada, gestiona las posiciones abiertas con los datos recibidos, pero bloquea nuevas entradas retroactivas.
2. Cada alta o reactivación guarda `activation_time`. Una señal solo puede abrir operación si la vela cerró después de ese instante. Las velas que ya estaban cerradas antes de activar TIC no generan operaciones.
3. `/tic_status` identifica que el timestamp mostrado corresponde a la apertura de la última vela cerrada procesada y avisa cuando falta la vela cerrada más reciente.
4. La detección de entrada exige perforación de banda y cierre en la dirección de la operación. Perforar la banda inferior y cerrar alcista no genera SHORT.

## Persistencia y compatibilidad

Se añade la columna `activation_time` a `tic_live_config`. La migración conserva configuraciones, checkpoints, operaciones y eventos. En bases existentes, el valor inicial se rellena desde `updated_at`; al volver a activar una suscripción se establece un nuevo instante de activación.

## Pruebas

Ejecutado `python -m unittest discover -v`: 54 pruebas correctas. También se verificó la migración de una tabla SQLite con el esquema anterior y la compilación de los módulos modificados.
