# Corrección TTR Live en Render

## Errores corregidos

- `ttr_live.py` importaba `sar`, `fractal_snapshots` y `cross_signal`, pero llamaba a `ttr_bar_action` sin importarla. Ahora importa también `ttr_bar_action` desde `ttr_pine`.
- El envío de notificaciones ahora es serializado y respeta el `retry_after` de Telegram como una pausa global para todas las notificaciones TTR del proceso.
- Los errores persistentes del monitor se registran y notifican una sola vez por símbolo y firma del error; se habilita una nueva notificación cuando el error cambia o el monitor vuelve a funcionar.

## Despliegue

1. Sustituir el código del servicio por esta versión.
2. Confirmar que `ttr_live.py` y `ttr_pine.py` se despliegan juntos en el mismo directorio/módulo Python.
3. Revisar los logs después del redeploy: ya no debe aparecer `name 'ttr_bar_action' is not defined`.
4. Si Telegram está todavía dentro de una ventana `RetryAfter` anterior, las notificaciones pueden seguir pausadas hasta que expire esa ventana. El proceso nuevo no debe insistir enviando mensajes durante el bloqueo.

## Seguridad

Si un token de bot aparece en logs, URLs, capturas o mensajes compartidos, debe revocarse en BotFather y sustituirse en las variables de entorno de Render. No guardar el token en el repositorio ni imprimirlo en logs.

## Render

El aviso de horas gratuitas es independiente de los errores Python y de Telegram. Revisar el consumo del workspace en Render; cuando el contador mensual se agota, los servicios gratuitos pueden suspenderse según las condiciones del plan.
