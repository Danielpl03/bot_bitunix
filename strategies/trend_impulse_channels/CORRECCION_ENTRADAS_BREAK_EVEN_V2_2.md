# Corrección TIC V2.2

## Cambio de canal
El evento de cambio/reinicio de canal se identifica con `bars_in_trend == 1`, que es el reinicio que realiza el Pine cuando se dispara `flip`. No se compara únicamente la dirección: un nuevo canal puede comenzar manteniendo `direction == 1` o `direction == -1`.

La vela que reinicia el canal queda excluida de nuevas entradas aunque:
- rompa la banda,
- cierre con cuerpo en la dirección de la operación,
- el canal nuevo conserve la dirección previa.

## Stop a break-even después de TP1
Cuando una operación alcanza TP1 al cierre de la vela de reinicio:
- se realiza el porcentaje configurado de la posición;
- el stop del remanente se actualiza al precio de entrada;
- el nuevo stop empieza a aplicarse desde la vela siguiente;
- si el precio vuelve al punto de entrada, se cierra el remanente y se conserva el PnL realizado en TP1.

El Excel muestra `SL inicial` y `SL actual` para distinguir el riesgo original del stop de protección actualizado.
