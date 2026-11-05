# Corrección de selección de TP/RE respecto a TTR Pine v6

## Cambios

- `ttr_pine.py`: `ttr_bar_action()` ya no descarta un fractal por `frac.when < pos.when`. Pine valida `frac.boTime >= pos.when`; un fractal formado antes de entrar puede generar TP/RE si su breakout fue posterior a la entrada.
- `ttr_pine.py`: los candidatos de TP/RE se recorren en el orden ascendente de los índices de la cola seleccionada, igual que los bucles `fracHHFrom..fracHHTo` y `fracLLFrom..fracLLTo` del Pine.
- `ttr_pine.py`: `fractal_snapshots()` reproduce el recorrido de breakout de `tac/21`: recorrido descendente, contador/límite temporal, uso de la expresión original `llBoLb` en `hhBoTimeLimit` y `insert(j, mismo_objeto)` para conservar la duplicación de referencia que hace Pine.
- `ttr_pine.py`: la condición de RE de BUY en la rama LL utiliza `sigReSellNoTp`, igual que el código Pine suministrado (se conserva el comportamiento original, incluso si el nombre parece inconsistente).
- Se añadieron pruebas de regresión para fractales anteriores a la entrada con breakout posterior, orden de selección y duplicación de referencias.

## Alcance y precauciones

No se modificaron entradas, reversiones, PSAR, porcentajes de reducción, PostgreSQL ni el esquema de datos. Tampoco se incrementó `PINE_ENGINE_VERSION`, para evitar que el mecanismo de reconciliación cierre las operaciones abiertas al desplegar esta corrección.

La paridad de TP se debe validar con las mismas velas OHLC, fuente de fractales y parámetros de TradingView. Esta corrección alinea los tres puntos señalados; no demuestra por sí sola que todos los posibles casos de Pine intrabar/repainting estén reproducidos.
