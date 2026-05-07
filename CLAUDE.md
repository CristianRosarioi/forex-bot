# Forex Trading Bot — Contexto del Proyecto

## Filosofía
Este bot es para GANAR, no para quemar cuentas. La gestión de riesgo es el pilar central, no una feature. Toda decisión técnica que comprometa la seguridad del capital se rechaza, sin importar la mejora de performance que prometa.

## Stack Técnico
- Python 3.11 (Windows)
- MetaTrader5 (lib oficial)
- Pandas, NumPy
- PostgreSQL 16 + SQLAlchemy 2 + psycopg2-binary
- Pydantic v2 + pydantic-settings
- Plotly para gráficas
- python-telegram-bot para notificaciones
- pyyaml para config de símbolos

## Broker
Pepperstone, vía MetaTrader 5 instalado en mini PC Windows.
Cuenta demo primero. Live solo después de mínimo 30 días en demo.

## Activos
Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD, GBPJPY, EURGBP
Índices: US500, NAS100

## Timeframes
Lectura: M1, M5, M15, H1, H4, D1
Entradas principales: M15, H1
Bias de tendencia: H4, D1
Operaciones: intradía (minutos a horas, máximo 24h)

## Estrategias (price action puro, sin indicadores)
1. Retest
2. Breakout
3. Fade/Failure

Las estrategias son PLUGINS. Heredan de bot.strategy.base.BaseStrategy.
NUNCA modifican el core. Si una estrategia necesita algo del core, se refactoriza el core para exponerlo como interfaz.

## Modos de Operación
- SHADOW: solo genera señales y las loggea (cero ejecución)
- PAPER: ejecución virtual contra precio live (cero MT5)
- DEMO: ejecución real en cuenta demo Pepperstone
- LIVE: cuenta real (último paso)

El modo se controla con la variable de entorno BOT_MODE en .env.

## REGLAS ABSOLUTAS — NUNCA VIOLAR

1. Toda orden DEBE pasar por bot/risk/validator.py antes de llegar a bot/execution/.
2. bot/risk/validator.py NUNCA se modifica sin invocar al subagente risk-auditor primero.
3. Toda orden DEBE tener stop loss. Sin SL, no hay orden.
4. Toda estrategia DEBE heredar de BaseStrategy y ser stateless.
5. Toda decisión del bot (señal generada, orden enviada, error) DEBE quedar registrada en PostgreSQL.
6. NUNCA hardcodear credenciales. Todo va en .env (que está en .gitignore).
7. Si el bot no puede conectar a la DB, NO ejecuta órdenes (fail-safe).
8. Si MT5 no responde por más de 30 segundos, el bot suspende ejecución y notifica por Telegram.
9. Las 5 capas de risk management son obligatorias y no negociables:
   - Por orden (SL, riesgo %, spread max, slippage max)
   - Por día (drawdown diario, max trades, max losses consecutivas)
   - Por semana/mes (drawdown semanal/mensual)
   - Correlación (pares correlacionados reducen sizing)
   - Protección de cuenta (kill switch si balance cae a X%)
10. NUNCA operar 30 minutos antes ni 60 minutos después de eventos económicos de alto impacto (Forex Factory).
11. Si hay duda sobre una decisión técnica, PREGUNTAR al usuario antes de implementar.
12. Este proyecto es INDEPENDIENTE del backtester. NUNCA importar código del backtester.

## Subagentes Disponibles
- risk-auditor: read-only, audita cualquier cambio en bot/risk/ o bot/execution/
- strategy-builder: especialista en crear nuevas estrategias como plugins
- debugger: diagnóstico de errores en producción

## Workflow de Desarrollo
1. Cada feature en su propia rama (feature/nombre)
2. Antes de mergear a main, correr /run-audit
3. Tests obligatorios para bot/risk/ y bot/execution/
4. Commits descriptivos con prefijo (feat:, fix:, refactor:, test:)
