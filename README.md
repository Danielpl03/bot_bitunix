# Bot de Telegram para análisis y backtesting de estrategias cripto

> **Documento de contexto para desarrolladores e IA.** Este README describe la arquitectura, la responsabilidad de cada archivo, las reglas conocidas de las estrategias, los comandos disponibles y las limitaciones que deben conservarse al modificar el proyecto. Debe leerse antes de proponer o implementar cambios.

## Estrategia RSI + media móvil del RSI: `/backtest_rsi`

La capa de indicador está en `indicators.py`; la regla de señal, el motor y la exportación están aislados en `strategy_rsi.py`, `backtest_rsi.py` y `xlsx_export_rsi.py`. El RSI sigue la fórmula del Pine Script v6 de TradingView (RMA de Wilder); la media se calcula sobre el RSI y puede ser SMA, SMA + Bollinger Bands, EMA, SMMA/RMA, WMA o VWMA. VWMA utiliza el volumen de las velas.

Reglas actuales:
- Al entrar en sobrecompra (RSI >= 70), se habilitan únicamente SHORT hasta que RSI entre en sobreventa (RSI <= 30); al entrar en sobreventa se habilitan únicamente LONG hasta sobrecompra.
- La primera entrada de cada régimen se genera por giro del RSI. Las entradas adicionales se generan con cruces RSI/media: LONG si RSI termina por debajo de 55; SHORT si termina por encima de 45.
- Opcionalmente, `--omitir-primer-cruce` descarta el primer cruce RSI/media válido de cada régimen y permite señales de cruce desde el segundo.
- Si el RSI sale de un extremo y vuelve a entrar en ese mismo extremo sin haber tocado el opuesto, se reinicia el giro inicial y el contador de cruces. Las operaciones que ya estaban abiertas no se cancelan por este reinicio.
- Entrada en la apertura de la vela siguiente. Se permiten varias operaciones simultáneas. TP fijo 2R.
- Stop por defecto en el mínimo/máximo de las últimas 10 velas, incluida la señal. Opcionalmente `--stop-mode pivot` utiliza el último pivote local confirmado; `--pivot-left` y `--pivot-right` determinan las velas a cada lado y `--pivot-buffer` define el colchón porcentual (por defecto 0.1%). Si no hay pivote confirmado, usa el extremo de la ventana como alternativa.
- Si una vela toca stop y objetivo, se considera stop primero. No se modelan comisiones, funding ni deslizamiento.

Ejemplos:
- `/backtest_rsi BTCUSDT 4h 500`
- `/backtest_rsi BTCUSDT 15m 2025-01-01 2025-01-31 --rsi 14 --tipo-media EMA --media 9 --stop 10`
- `/backtest_rsi BTCUSDT 1h 1000 --stop-mode pivot --pivot-left 2 --pivot-right 2 --pivot-buffer 0.1 --omitir-primer-cruce`

Parámetros: `--rsi N`, `--tipo-media SMA|EMA|RMA|WMA|VWMA|BB`, `--media N`, `--stop N`, `--stop-mode lookback|pivot`, `--pivot-left N`, `--pivot-right N`, `--pivot-buffer PORCENTAJE`, `--omitir-primer-cruce`. `--zonas-extremas` se conserva por compatibilidad, pero el régimen 30/70 ya forma parte de la estrategia. El comando produce estadísticas y un Excel con resumen, operaciones y velas. Las estrategias anteriores permanecen en sus propios módulos y comandos.

Pruebas unitarias: `pytest -q` (incluye pruebas del indicador, regímenes, reinicio de extremo, pivotes, cruces opcionales y operaciones simultáneas).

## 1. Objetivo del proyecto

El proyecto es un bot de Telegram escrito en Python que consulta datos de mercado de Bitunix, calcula indicadores técnicos, informa sobre señales y permite ejecutar backtests de varias estrategias sobre velas históricas. También incluye funciones para consultar datos privados de la cuenta de futuros y abrir/cerrar posiciones mediante Bitunix.

La necesidad principal actual es **investigar de forma reproducible estrategias de trading y comparar sus resultados**, sin mezclar las reglas de entrada de una estrategia con las de otra. La rentabilidad no está demostrada: los backtests actuales no incorporan todos los costes reales de trading.

El proyecto debe permitir probar símbolos y temporalidades distintas. Los intervalos aceptados por el bot son: `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w` y `1M`. **Nota técnica:** el intervalo mensual `1M` está en la lista aceptada, pero el mapa de duración usado para consultar rangos de fechas en `backtest_common.py` no incluye `1M`; el modo por cantidad de velas puede funcionar, mientras que el modo por fechas necesita que se implemente esa duración.

## 2. Estado funcional actual

Hay backtests registrados como comandos separados:

1. `/backtest`: estrategia original basada en Bollinger + Estocástico.
2. `/backtest_keltner`: estrategia de cruces del Canal de Keltner.
3. `/backtest_reversion`: estrategia experimental de reversión a la media con Bollinger + Estocástico.
4. `/backtest_squeeze`: ruptura tras compresión de volatilidad con Bollinger + Keltner.
5. `/backtest_dual_thrust`: ruptura de rango Dual Thrust con reversión ante señal opuesta.
6. `/backtest_rsi`: estrategia RSI por regímenes 30/70, giro inicial y cruces RSI/media opcionales.

Las cuatro estrategias comparten cliente de datos de Bitunix, utilidades de carga de velas y parte de la infraestructura de gestión de riesgo/exportación, pero mantienen lógicas de entrada y simulación distintas.

### 2.1 Estrategia original: `/backtest`

La estrategia original está implementada en `strategy.py` y su simulación en `backtest.py`.

A nivel general combina cruces del Estocástico con la posición del cierre respecto a las Bandas de Bollinger, la dirección de la vela y la ubicación del stop. Sus reglas exactas están documentadas en el docstring de `strategy.py`; **no sustituirlas por las reglas de la estrategia de reversión**.

Admite filtros opcionales de señales:

- `--filtro-rango VELAS LIMITE`: limita la densidad de señales dentro de una ventana de velas.
- `--filtro-banda RATIO`: exige que haya espacio hasta la banda contraria superior a `RATIO` veces el riesgo hasta el stop.
- `--filtro-anchura PORCENTAJE`: exige una anchura mínima del canal expresada como porcentaje del precio.
- `--riesgo fijo USDT` o `--riesgo porcentaje PORCENTAJE CAPITAL_USDT`: configura los cálculos auxiliares de riesgo y exposición.

Los filtros de banda y anchura utilizan los atributos de banda y precio de las señales producidas por esta estrategia. Todos los filtros están desactivados por defecto.

### 2.2 Estrategia Keltner: `/backtest_keltner`

Implementada en `strategy_keltner.py` y simulada en `backtest_kelner.py` (el nombre del archivo contiene históricamente la grafía `kelner`; no renombrarlo sin actualizar todos los imports).

Reglas generales:

- LONG: la vela cruza de abajo hacia arriba la banda inferior o la media del Canal de Keltner.
- SHORT: la vela cruza de arriba hacia abajo la banda superior o la media.
- La detección del cruce usa apertura y cierre; una mecha aislada no cuenta como cruce.
- El stop inicial se coloca en el mínimo de la vela de señal para LONG o el máximo para SHORT.
- El backtest estudia objetivos por múltiplos de riesgo (R); la lógica y las estadísticas concretas están en `backtest_kelner.py`.

Opciones opcionales:

- `--solo-bandas-limite`: LONG solo cruza la banda inferior y SHORT solo cruza la superior.
- `--filtro-rango VELAS LIMITE`.
- `--filtro-banda RATIO`.
- `--filtro-anchura PORCENTAJE`.
- `--riesgo fijo USDT` o `--riesgo porcentaje PORCENTAJE CAPITAL_USDT`.

Los filtros son opcionales y no se aplican por defecto. No asumir que el modo de bandas límite está activado si el parámetro no aparece.

### 2.3 Estrategia experimental de reversión: `/backtest_reversion`

Es la primera estrategia del nuevo ciclo de investigación. Se añadió como módulo separado para poder comparar su hipótesis con las estrategias anteriores sin alterar estas últimas.

Archivos principales: `strategy_reversion.py`, `backtest_reversion.py` y `xlsx_export_reversion.py`.

Parámetros iniciales de indicadores:

- Bandas de Bollinger: período 20, desviación 2.
- Estocástico lento: `14, 3, 3`.
- Sobreventa: 20.
- Sobrecompra: 80.
- Ventana del stop: 10 velas.

Reglas actuales (modo original, predeterminado):

- **LONG:** el cierre de la vela de señal está por debajo de la banda inferior de Bollinger; `%K` cruza al alza `%D`; ambas líneas están por debajo de 20; el stop calculado con el mínimo de las últimas 10 velas queda debajo del cierre y la banda media está por encima del cierre.
- **SHORT:** el cierre está por encima de la banda superior; `%K` cruza a la baja `%D`; ambas líneas están por encima de 80; el stop calculado con el máximo de las últimas 10 velas queda por encima del cierre y la banda media debajo del cierre.

Variante opcional de comparación: `--recuperacion-banda`.

- **LONG:** durante las últimas 5 velas el precio debe haber tocado/perforado la banda inferior (mínimo de vela <= banda inferior de esa misma vela) o haber cerrado por debajo de ella. La vela de señal debe cerrar otra vez por encima de la banda inferior, y el Estocástico debe confirmar cruce alcista con `%K` y `%D` bajo 20.
- **SHORT:** regla simétrica: durante las últimas 5 velas el precio toca/perfora o cierra por encima de la banda superior; la vela de señal cierra otra vez por debajo de la banda superior y el Estocástico confirma cruce bajista con ambas líneas sobre 80.
- El lookback compara cada vela con sus propias bandas de Bollinger calculadas hasta esa vela, no con las bandas actuales.
- El modo de recuperación no cambia el modo original ni lo activa por defecto; se selecciona añadiendo el flag al comando. Las reglas de stop, objetivo, ejecución en la siguiente apertura y simulación siguen siendo las mismas.
- La señal se conoce al cierre de una vela y la entrada del backtest se intenta en la **apertura de la siguiente vela**, para no simular una entrada retrospectiva al cierre de la señal.
- El objetivo es la banda media de Bollinger calculada en la vela de señal y queda fijo durante esa operación.
- Solo puede existir una operación simultánea por símbolo en esta simulación.
- Si la apertura de la vela de entrada ya invalida el orden lógico entre stop, entrada y objetivo, se descarta la operación.
- Si una misma vela toca stop y objetivo y el OHLC no permite conocer cuál se alcanzó primero, se considera el stop primero (supuesto conservador).
- Si se indica un horizonte máximo y no se toca ni stop ni objetivo, la operación se cierra al cierre de la última vela del horizonte. Sin horizonte, puede quedar pendiente al final del histórico.

Esta estrategia **no acepta los filtros heredados** `--filtro-rango`, `--filtro-banda`, `--filtro-anchura` ni `--solo-bandas-limite`: el comando devuelve un aviso si se intentan usar. La opción de riesgo se usa para el cálculo auxiliar/exportación, pero las estadísticas base de esta estrategia se expresan en R y variación porcentual.

No se debe describir esta estrategia como rentable hasta medirla con costes y en datos fuera de muestra.


### 2.5 Dual Thrust Breakout: `/backtest_dual_thrust`

Implementada de forma independiente en `strategy_dual_thrust.py`, `backtest_dual_thrust.py` y `xlsx_export_dual_thrust.py`, siguiendo el primer sistema de la investigación adjunta.

**Reglas base:**
- `HH`/`LL`: máximo/mínimo de los máximos/mínimos de las N velas anteriores.
- `HC`/`LC`: máximo/mínimo de los cierres de las N velas anteriores.
- `Range = max(HH - LC, HC - LL)`.
- `BuyTrack = Open_actual + K1 × Range`; `SellTrack = Open_actual - K2 × Range`.
- Una ruptura intrabar alcista abre largo; una ruptura bajista abre corto. Si existe posición opuesta, se cierra y se invierte.
- Valores iniciales: N=4, K1=0.6, K2=0.7. Son valores de partida dentro de los rangos sugeridos, no una optimización ni una garantía de rentabilidad.
- Si una misma vela alcanza ambos niveles, se omite la señal de ruptura porque OHLC no permite conocer el orden intrabar. Si vence un horizonte externo, la posición se cierra en la apertura de esa vela, incluso si la vela resulta ambigua.
- Los precios de entrada/salida incluyen deslizamiento adverso configurable por lado. Las comisiones se descuentan del PnL neto de cada operación. Las tasas reales dependen de la cuenta y deben configurarse; no se inventa una tarifa Bitunix.
- La financiación/funding no se modela porque requiere las tasas históricas aplicables al contrato y a cada instante.
- El Excel incorpora PnL bruto (ya afectado por slippage), comisiones, deslizamiento estimado, PnL neto, rentabilidad compuesta y una hoja de curva de capital con drawdown calculado sobre máximos previos. La curva teórica supone reinvertir el 100% del capital por operación, sin apalancamiento; no es un cálculo de margen ni una simulación del saldo real de futuros.
- Las posiciones abiertas al final se muestran como pendientes y no se incluyen en la rentabilidad realizada ni en la curva de capital.
- El trailing stop de 1.5 × ATR(14) se deja desactivado en la variante base porque la investigación lo plantea como protección opcional. La estrategia tampoco define stop-loss ni tamaño de posición por riesgo.

**Uso:**

```text
/backtest_dual_thrust BTCUSDT 4h 1000
/backtest_dual_thrust BTCUSDT 4h 1000 50 --n 4 --k1 0.6 --k2 0.7 --fee-bps 5 --slippage-bps 2
/backtest_dual_thrust BTCUSDT 4h 1000 --subintervalo 1h --n 4 --k1 0.6 --k2 0.7 --fee-bps 5 --slippage-bps 2
/backtest_dual_thrust BTCUSDT 4h 2025-01-01 2025-06-30 --subintervalo 1h --n 4 --k1 0.6 --k2 0.7 --fee-bps 5 --slippage-bps 2
```

`--fee-bps` y `--slippage-bps` son puntos básicos **por lado** (1 bp = 0,01 %); ambos valen 0 por defecto porque debes indicar una estimación acorde a tu cuenta y mercado. El ejemplo 5/2 es solo un escenario de prueba, no una afirmación sobre las tarifas de Bitunix.

#### Ejecución con subvelas (`--subintervalo`)

Se puede mantener la temporalidad de la estrategia y usar una temporalidad menor para reconstruir la secuencia intrabar. Por ejemplo, con `4h --subintervalo 1h`, los niveles Dual Thrust se calculan usando velas de 4h y cada vela de 4h se construye a partir de cuatro velas de 1h completas; las velas de 1h se recorren cronológicamente para ejecutar entradas y reversiones. Se admiten intervalos inferiores que dividan exactamente la temporalidad objetivo (por ejemplo, `4h` con `1h`, `30m` o `15m`). Las velas objetivo incompletas o con subvelas faltantes se descartan.

Si una misma subvela inferior toca ambos niveles, el orden sigue siendo desconocido y esa subvela se omite; las subvelas posteriores de la misma vela objetivo se procesan normalmente. Si la subvela abre más allá del nivel de ruptura, la ejecución se simula en su apertura para reflejar el gap. Los timestamps de las operaciones corresponden a la temporalidad de ejecución, mientras que los niveles y el horizonte se calculan en la temporalidad objetivo.

Los parámetros aceptan N entre 2 y 10, y K1/K2 entre 0.3 y 1.8, conforme a los rangos de búsqueda del documento. La estrategia utiliza su propio motor y no aplica los filtros genéricos de las demás estrategias.

**Validación y comparación:**
- Compara activos y temporalidades sobre exactamente las mismas fechas UTC, no sobre el mismo número de velas (1000 velas de 1h y 4h cubren horizontes distintos).
- Reserva un tramo cronológico posterior como fuera de muestra. No ajustes parámetros usando ese tramo; ejecuta el mismo comando sobre ese rango solo después de fijar N/K1/K2.
- Ejecuta primero sin costes para compararlo con resultados anteriores y luego con comisiones/deslizamiento realistas. Las estadísticas netas son las relevantes para valorar viabilidad.
- Las pruebas automatizadas están en `test_dual_thrust.py` y `test_dual_thrust_multitimeframe.py`; ejecuta `python -m unittest -v test_dual_thrust test_dual_thrust_multitimeframe`.

### 2.4 Squeeze Bollinger + Keltner: `/backtest_squeeze`

Implementada de forma independiente en `strategy_squeeze.py`, `backtest_squeeze.py` y `xlsx_export_squeeze.py`. Busca rupturas posteriores a una compresión de volatilidad.

Parámetros iniciales:
- Bollinger: período 20, desviación 2.
- Keltner: EMA 20, ATR 20 y multiplicador 1.5.
- Momentum direccional: cambio del cierre respecto al de hace 12 velas.
- Stop: mínimo/máximo de las últimas 10 velas, incluida la señal.
- Objetivo: 2R fijo.

Reglas de señal:
- La vela anterior debe tener las bandas de Bollinger completamente dentro del Canal de Keltner (squeeze).
- En la vela actual debe haberse liberado la compresión.
- LONG: cierre por encima de las bandas superior de Bollinger y Keltner, con momentum positivo.
- SHORT: cierre por debajo de las bandas inferiores de ambos canales, con momentum negativo.
- La señal se calcula al cierre; la entrada se intenta en la apertura de la siguiente vela.
- Si la apertura ya está más allá del stop, se descarta la entrada. Solo una operación simultánea; si una vela toca stop y objetivo, se considera stop primero.

Es una definición inicial explícita para probar la hipótesis de ruptura, no una estrategia validada ni una promesa de rentabilidad. No incorpora comisiones, financiación ni deslizamiento. El comando rechaza filtros heredados que no forman parte de estas reglas.

## 3. Comandos de Telegram

### 3.1 Comandos generales

- `/start`: ayuda y listado de comandos.
- `/price [SIMBOLO]`: consulta precio/ticker.
- `/watchlist`: ver o editar la lista de símbolos para `/price`.
- `/indicators SIMBOLO [INTERVALO]`: muestra valores de indicadores.
- `/alerts`: administra o consulta símbolos vigilados por alertas de la estrategia clásica y RSI.
- `/balance [MONEDA]`: consulta balance privado de futuros.
- `/positions [SIMBOLO]`: consulta posiciones abiertas.
- `/open SIMBOLO BUY|SELL CANTIDAD`: solicita abrir una posición; revisar cuidadosamente antes de usar.
- `/close POSITION_ID`: solicita cerrar una posición; revisar cuidadosamente antes de usar.

Los comandos privados requieren credenciales de Bitunix. El bot puede enviar botones de confirmación para las acciones de apertura/cierre; son operaciones potencialmente reales, no un simulador.

#### Suscripciones del Job de alertas

El Job programado revisa las estrategias al cierre de las velas de 1h y 4h. Las suscripciones de la estrategia clásica y RSI son independientes:

- `/alerts add BTCUSDT` / `/alerts remove BTCUSDT`: estrategia clásica existente.
- `/alerts rsi add BTCUSDT`: suscribe BTCUSDT a la estrategia RSI por regímenes en 1h y 4h.
- `/alerts rsi remove BTCUSDT`: elimina esa suscripción RSI.
- `/alerts rsi`: lista las monedas suscritas a RSI.
- `/alerts rsi check [BTCUSDT] [1h|4h]`: consulta RSI/media y si hay una señal reciente.
- `/alerts rsi reset`: vacía únicamente la lista RSI.

La alerta RSI se evalúa sobre la última vela cerrada. Incluye LONG/SHORT, tipo de señal (giro RSI o cruce de la media), RSI, media, stop sugerido y objetivo teórico 2R. La entrada es de referencia para la apertura de la vela siguiente; el Job alerta, no ejecuta órdenes. Se guarda el último cierre procesado por símbolo e intervalo para evitar repetir la alerta de una misma vela.

### 3.2 Formato de backtests

Formato por cantidad de velas:

```text
/backtest SIMBOLO INTERVALO NUM_VELAS [HORIZONTE]
/backtest_keltner SIMBOLO INTERVALO NUM_VELAS [HORIZONTE]
/backtest_reversion SIMBOLO INTERVALO NUM_VELAS [HORIZONTE] [--recuperacion-banda]
/backtest_squeeze SIMBOLO INTERVALO NUM_VELAS [HORIZONTE]
```

Formato por fechas UTC inclusivas:

```text
/backtest SIMBOLO INTERVALO AAAA-MM-DD AAAA-MM-DD [HORIZONTE]
/backtest_keltner SIMBOLO INTERVALO AAAA-MM-DD AAAA-MM-DD [HORIZONTE]
/backtest_reversion SIMBOLO INTERVALO AAAA-MM-DD AAAA-MM-DD [HORIZONTE] [--recuperacion-banda]
/backtest_squeeze SIMBOLO INTERVALO AAAA-MM-DD AAAA-MM-DD [HORIZONTE]
```

Ejemplos:

```text
/backtest_reversion BTCUSDT 4h 1000
/backtest_squeeze BTCUSDT 1h 1000
/backtest_squeeze ETHUSDT 4h 2025-01-01 2025-06-30
/backtest_reversion ETHUSDT 15m 2025-01-01 2025-03-31
/backtest_reversion BTCUSDT 4h 500 20
/backtest_keltner BTCUSDT 4h 500 --solo-bandas-limite --filtro-banda 2
/backtest BTCUSDT 1h 500 --filtro-rango 10 3 --filtro-anchura 1.5
/backtest BTCUSDT 4h 500 --riesgo fijo 10
/backtest BTCUSDT 4h 500 --riesgo porcentaje 1 1000
```

El parámetro `HORIZONTE`, cuando se proporciona, es un número máximo de velas que dura una operación; no es la duración del histórico. El parser limita la cantidad de velas al rango permitido por el bot (actualmente máximo 1500 para comandos de backtest). Los rangos de fechas están limitados por `backtest_common.py` a un máximo aproximado de 10.000 velas incluyendo calentamiento y seguimiento. La carga histórica añade velas previas para calentar indicadores y velas posteriores para observar salidas; las señales se cuentan dentro del período pedido.

### 3.3 Indicadores disponibles

Los cálculos principales están en `indicators.py`:

- Bandas de Bollinger.
- Estocástico `%K` y `%D`.
- EMA, ATR y serie del Canal de Keltner.

## 4. Arquitectura y flujo de datos

Flujo típico de un backtest:

1. Telegram recibe el comando en `bot.py`.
2. El comando analiza símbolo, intervalo, rango histórico, horizonte y opciones.
3. `backtest_common.py` coordina la carga de velas históricas desde `bitunix_client.py`, el calentamiento de indicadores y la delimitación del período.
4. `indicators.py` calcula series de indicadores.
5. El módulo `strategy*.py` determina las señales sin conocer Telegram ni realizar llamadas a la API.
6. El módulo `backtest*.py` simula entradas/salidas y calcula resultados.
7. `risk_management.py` calcula tamaños orientativos de posición, PnL en USDT y estimaciones relacionadas con riesgo/margen para los informes.
8. `xlsx_export*.py` genera el archivo Excel en memoria.
9. `bot.py` envía el resumen y el Excel al chat de Telegram.

**Principio de diseño:** los módulos de estrategia deben permanecer lo más puros posible: reciben datos numéricos ya ordenados y devuelven señales; no deben hacer peticiones a Telegram ni a Bitunix. Las simulaciones y exportadores se mantienen separados.

## 5. Inventario de archivos

### `bot.py`
Punto de entrada principal y capa de Telegram. Registra comandos, valida/normaliza argumentos, controla la autorización por `TELEGRAM_ALLOWED_USER_ID`, consulta datos a través del cliente, invoca backtests, formatea estadísticas y envía informes/Excel. También contiene las funciones de alertas y las operaciones privadas de cuenta.

### `run_local.py`
Entrada para ejecución local. Carga variables de entorno desde un archivo `.env` situado en la misma carpeta y después importa/ejecuta `bot.main()`.

### `bitunix_client.py`
Cliente HTTP para Bitunix. Consulta tickers y velas de mercado públicas y ofrece endpoints privados para balance, posiciones y órdenes. Gestiona la firma de solicitudes privadas. La carga de datos de mercado no necesita claves privadas; las funciones de cuenta/órdenes sí.

### `indicators.py`
Implementaciones numéricas de Bollinger, Estocástico, EMA, ATR y Canal de Keltner. Es la base compartida de indicadores. Los cambios aquí pueden afectar varias estrategias y deben validarse con pruebas de regresión.

### `strategy.py`
Reglas de la estrategia original Bollinger + Estocástico. Expone estructuras de señal/condiciones y funciones para evaluar la última vela, detectar señal y calcular niveles de take-profit por múltiplos de R.

### `backtest.py`
Motor de simulación y estadísticas de la estrategia original. Evalúa resultados de operaciones y escenarios con distintos ratios de take-profit.

### `strategy_keltner.py`
Reglas de la estrategia de cruces del Canal de Keltner. Incluye selección de cruces de bandas externas mediante `only_outer_bands` y objetos de estado/señal.

### `backtest_kelner.py`
Motor de backtest de Keltner. El nombre está escrito `kelner` en el repositorio y así lo importa `bot.py`. Calcula operaciones, resultados y curvas/escenarios por ratios.

### `strategy_reversion.py`
Reglas de entrada de la nueva estrategia de reversión Bollinger + Estocástico. Calcula la señal al cierre de la vela actual y determina stop y objetivo iniciales a partir del histórico disponible hasta esa vela.

### `backtest_reversion.py`
Simula las operaciones de reversión. Ejecuta al open de la siguiente vela, sigue stop/objetivo/horizonte, impide solapamiento de operaciones por símbolo y calcula estadísticas en R, porcentaje, profit factor y drawdown.

### `backtest_common.py`
Infraestructura compartida de backtesting: parseo de períodos, conversión de fechas UTC, descarga de velas, calentamiento y seguimiento, filtrado por período y filtros opcionales de densidad, espacio hasta banda contraria y anchura del canal.

### `risk_management.py`
Configuración común de riesgo y funciones para estimar tamaño de posición, PnL en USDT y margen. Las estimaciones de margen son aproximadas: el código documenta que no reproduce todos los tiers de mantenimiento, comisiones ni precio de marca de Bitunix.

### `xlsx_export.py`
Genera el libro Excel del backtest original. Incluye hojas de resumen, señales/operaciones, velas históricas, escenarios de riesgo y configuración de filtros.

### `xlsx_export_keltner.py`
Genera el libro Excel del backtest Keltner, con resumen, operaciones, velas, curva/escenarios y hoja de riesgo.

### `xlsx_export_reversion.py`
Genera el Excel del backtest de reversión con los resultados y operaciones de esa estrategia, más información de riesgo y datos históricos.

### Archivos de estado creados en ejecución

- `watchlist.json`: lista de símbolos para la función de watchlist; la ruta puede cambiar con `WATCHLIST_FILE`.
- `alerts_watchlist.json`: lista de símbolos de la estrategia clásica; ruta configurable con `ALERTS_FILE`.
- `rsi_alerts_watchlist.json`: lista de símbolos suscritos a alertas RSI; ruta configurable con `RSI_ALERTS_FILE`.
- `rsi_alert_state.json`: última vela procesada por símbolo/intervalo para evitar alertas RSI duplicadas; ruta configurable con `RSI_ALERT_STATE_FILE`.
- `.env`: configuración local de secretos/variables de entorno. No debe subirse al control de versiones ni compartirse con una IA si contiene credenciales.

Estos archivos no están incluidos necesariamente en el ZIP de código; se crean o configuran según la instalación.

## 6. Configuración y dependencias

Variables de entorno utilizadas:

- `TELEGRAM_BOT_TOKEN`: token del bot de Telegram (obligatorio para ejecutar el bot).
- `TELEGRAM_ALLOWED_USER_ID`: ID numérico de Telegram autorizado. Muy recomendado. Si no se define, el código permite el acceso a cualquier usuario a los comandos; no es adecuado para producción.
- `BITUNIX_API_KEY`: clave API privada de Bitunix para operaciones de cuenta.
- `BITUNIX_API_SECRET`: secreto API privado de Bitunix.
- `WATCHLIST_FILE`: ruta opcional para la lista de símbolos de precio.
- `ALERTS_FILE`: ruta opcional para la lista de alertas de la estrategia clásica.
- `RSI_ALERTS_FILE`: ruta opcional para la lista de suscripciones RSI.
- `RSI_ALERT_STATE_FILE`: ruta opcional para el estado de deduplicación de alertas RSI.
- `PORT` y `RENDER_EXTERNAL_URL`: variables usadas para el despliegue en Render/webhook cuando corresponda.

El ZIP actual no contiene `requirements.txt`. Por los imports del código, las dependencias externas principales son `python-telegram-bot`, `requests`, `python-dotenv` y `openpyxl`; la versión exacta compatible no está fijada en este README. Antes de preparar un despliegue reproducible conviene crear un `requirements.txt` con versiones probadas.

Ejemplo de `.env` local (sustituir valores y no publicar este archivo):

```dotenv
TELEGRAM_BOT_TOKEN=REEMPLAZAR_CON_TOKEN
TELEGRAM_ALLOWED_USER_ID=REEMPLAZAR_CON_ID_NUMERICO
BITUNIX_API_KEY=
BITUNIX_API_SECRET=
```

Instalación orientativa en un entorno virtual:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
python -m pip install python-telegram-bot requests python-dotenv openpyxl
python run_local.py
```

El bot también puede iniciarse con `python bot.py` si las variables de entorno ya están exportadas. `run_local.py` es preferible en local porque carga `.env`.

## 7. Limitaciones actuales que una IA debe conservar y señalar

1. **Rentabilidad no demostrada.** Las estrategias son hipótesis de investigación, no recomendaciones financieras.
2. **Costes incompletos.** Los backtests no descuentan de forma integral comisiones, funding ni deslizamiento. Los resultados deben considerarse preliminares hasta incorporarlos.
3. **Datos OHLC.** Cuando una misma vela alcanza stop y objetivo, el orden intravela es desconocido; en reversión se adopta stop primero.
4. **Riesgo/margen aproximado.** `risk_management.py` declara que la estimación no replica exactamente los tiers de margen de mantenimiento ni todos los detalles de Bitunix.
5. **Temporalidad mensual.** `1M` está en la lista de intervalos aceptados, pero falta su duración en `INTERVAL_MS` para backtests por fechas.
6. **Sin manifiesto de dependencias.** El proyecto no contiene `requirements.txt` en el paquete actual.
7. **Pruebas limitadas.** No se incluye una suite formal de pruebas automatizadas en este paquete. Antes de afirmar que un cambio funciona, ejecutar compilación, pruebas unitarias y un smoke test con datos sintéticos; las pruebas de API real requieren credenciales/red y deben separarse de pruebas de órdenes.
8. **No ejecutar operaciones reales para probar estrategias.** Usar backtests o entornos de prueba; los comandos `/open` y `/close` pueden actuar sobre la cuenta real de futuros.
9. **Sin look-ahead.** Las señales se calculan con la información disponible al cierre de la vela de señal; cuando la estrategia lo define, la ejecución se hace en la siguiente apertura. No utilizar máximos, mínimos o indicadores futuros para decidir una entrada pasada.
10. **No mezclar reglas entre estrategias.** Los filtros heredados de Bollinger/Keltner no deben aplicarse automáticamente a `/backtest_reversion`; el código actual los rechaza para ese comando.

## 8. Próximas líneas de desarrollo acordadas

La intención de investigación es desarrollar y comparar tres enfoques independientes:

1. **Reversión:** Bollinger + Estocástico. Es el primer módulo experimental actual (`/backtest_reversion`).
2. **Ruptura de volatilidad (Squeeze):** Bollinger + Keltner, implementada en `/backtest_squeeze`; hipótesis aún no validada.
3. **Continuación de tendencia:** retroceso en Keltner + Estocástico. Aún no está implementada como estrategia separada en este paquete.

La siguiente tarea es evaluar la estrategia de reversión con datos históricos y mejorar la calidad del backtest antes de desarrollar las otras dos. Se recomienda que toda nueva estrategia tenga su propio módulo `strategy_*.py`, motor `backtest_*.py` y exportador `xlsx_export_*.py`, reutilizando las utilidades comunes cuando la semántica coincida.

## 9. Guía de trabajo para una IA que reciba este README

Al continuar el desarrollo:

1. Lee primero `README.md`, `bot.py` y los módulos de la estrategia que se va a tocar.
2. Inspecciona el código real antes de dar por cierta una descripción; este README resume el estado documentado y puede quedar desactualizado tras cambios futuros.
3. Mantén cada estrategia aislada y evita alterar el comportamiento de comandos existentes sin una petición explícita.
4. Antes de cambiar argumentos de Telegram, actualiza validación, mensajes de ayuda, ejemplos y pruebas.
5. Evita introducir sesgo de anticipación: documenta cuándo se conoce la señal y cuándo se simula la ejecución.
6. Incluye comisiones, deslizamiento y funding como parámetros explícitos cuando se implementen; no los supongas iguales a cero sin avisarlo.
7. Prueba los casos de LONG, SHORT, stop, objetivo, salida por horizonte, operación pendiente, vela que toca stop y objetivo, fechas UTC y ausencia de datos suficientes.
8. No inventes métricas de rentabilidad ni afirmes que una estrategia está validada sin ejecutar el backtest y analizar los resultados.
9. Nunca incluyas tokens, claves privadas ni secretos en código, README, logs, Excel o mensajes.
10. Si modificas archivos, actualiza este README para que siga sirviendo como contexto de transferencia a otra IA.

---

**Fin del documento de contexto.** Este archivo explica el propósito y las responsabilidades de los módulos; el código fuente sigue siendo la fuente definitiva para el comportamiento exacto.


## Trend Trader-Remastered (TTR)

Comando Telegram: `/backtest_ttr BTCUSDT 1h 1000 --re-entry true --tp 40,30,30 --dir BuyOnly --fee 0.05 --slippage 0.02`. `--re-entry false` ignora las entradas RE; `--tp` asigna el porcentaje de posición por cada señal TP y su suma no puede superar 100. En reversión se cierra la posición restante y se abre la dirección contraria. El stop trailing usa el lagging PSAR calculado al cierre y se aplica desde la siguiente vela.
