# Cambios RSI: reentradas después de SL y divergencias

## Reglas nuevas

- Se conserva la entrada inicial: primer giro del RSI tras activar el régimen extremo.
- Tras esa primera entrada, el motor puede generar candidatos de reentrada sin exigir que el RSI abandone y vuelva a entrar en la zona extrema:
  - `RSI_REENTRY`: nuevo giro local del RSI en la misma dirección del régimen.
  - `RSI_DIVERGENCE`: divergencia regular confirmada entre precio y RSI (bullish: precio hace lower-low y RSI higher-low; bearish: precio hace higher-high y RSI lower-high).
- Los candidatos de reentrada no son entradas automáticas: backtest y RSI Live solo los aceptan si la operación anterior del mismo símbolo/temporalidad cerró por `SL`, la señal aparece después del stop y no queda otra posición abierta.
- Cada nuevo SL vuelve a habilitar una oportunidad de reentrada.
- El stop sigue siendo estructural: pivote confirmado con el buffer configurado; si no existe pivote, se usa el extremo local desde el patrón de reentrada.
- El TP conserva la regla existente y se ejecuta al cierre de la vela que hace salir al RSI de la zona extrema opuesta: LONG `RSI previo >= overbought` y `RSI actual < overbought`; SHORT `RSI previo <= oversold` y `RSI actual > oversold`.

## Archivos

- `strategy_rsi.py`: generación de señales iniciales, nuevos giros y divergencias.
- `backtest_rsi.py`: simulación secuencial de SL/TP y filtro de reentradas únicamente después de SL.
- `rsi_live.py`: consulta del último cierre y filtro persistente de reentrada en paper trading.
- `test_rsi_strategy.py`, `test_rsi_live.py`: cobertura de giro repetido, divergencia y habilitación por SL.
