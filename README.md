# Forex Trading Bot

Bot de trading automatizado 24/5 para Forex e índices. Conecta con Pepperstone vía MetaTrader 5.

## Características
- Multi-activo (Forex + índices)
- Multi-timeframe (M1 a D1)
- Estrategias como plugins (Retest, Breakout, Fade/Failure)
- 5 capas de gestión de riesgo
- Modos: SHADOW / PAPER / DEMO / LIVE
- Notificaciones Telegram
- Logging completo en PostgreSQL
- Reportes de rendimiento automáticos

## Stack
Python 3.11 · MetaTrader5 · PostgreSQL 16 · SQLAlchemy 2 · Pydantic v2

## Setup (Windows)

```bash
# 1. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
copy .env.example .env
# Editar .env con tus credenciales

# 4. Inicializar base de datos
python scripts/init_db.py

# 5. Verificar conexión MT5 y DB
python scripts/check_connection.py

# 6. Arrancar en modo SHADOW
python scripts/start_bot.py
```

## Modos de Operación
| Modo | Descripción |
|------|-------------|
| SHADOW | Solo genera señales, no ejecuta |
| PAPER | Ejecución virtual contra precio live |
| DEMO | Cuenta demo de Pepperstone |
| LIVE | Cuenta real (último paso) |

## Estructura
Ver `CLAUDE.md` para arquitectura y reglas del proyecto.

## Licencia
Privado.
