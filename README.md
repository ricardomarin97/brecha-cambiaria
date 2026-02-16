# Brecha Cambiaria Venezuela

Aplicación web que muestra en tiempo real la brecha cambiaria entre el USDT (Binance P2P), el dólar BCV y el euro BCV en Venezuela.

## Características

- **Tasas en tiempo real**: Actualización automática cada 60 segundos
- **Datos de múltiples fuentes**: BCV oficial y Binance P2P
- **Cálculo de brechas**: USDT vs Dólar BCV, USDT vs Euro BCV, Euro vs Dólar BCV
- **Calculadora de cambio**: Convierte entre VES, USD, EUR y USDT
- **Historial de precios**: Gráficas interactivas con filtros de tiempo
- **Bot de Telegram**: Notificaciones automáticas y alertas de cambio
- **PWA Ready**: Funciona como aplicación instalable

## Arquitectura

```
┌─────────────────┐     ┌─────────────────┐
│   Binance P2P   │     │    BCV.gob.ve   │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     │
              ┌──────▼──────┐
              │   Flask     │
              │   Backend   │
              └──────┬──────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
    ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
    │ Web UI  │ │ Telegram│ │PostgreSQL│
    │ (HTML)  │ │   Bot   │ │   DB     │
    └─────────┘ └─────────┘ └──────────┘
```

## Tecnologías

- **Backend**: Python, Flask, APScheduler
- **Frontend**: HTML, CSS, JavaScript, Chart.js
- **Base de datos**: PostgreSQL (producción) / JSON (desarrollo)
- **Bot**: python-telegram-bot
- **Hosting**: Render

## Variables de Entorno

| Variable | Descripción |
|----------|-------------|
| `DATABASE_URL` | URL de conexión a PostgreSQL |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram |

## Instalación Local

1. **Clonar el repositorio**
```bash
git clone https://github.com/tuusuario/brecha-cambiaria.git
cd brecha-cambiaria
```

2. **Crear entorno virtual**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

3. **Instalar dependencias**
```bash
pip install -r requirements.txt
```

4. **Configurar variables de entorno**
```bash
cp .env.example .env
# Editar .env con tus valores
```

5. **Ejecutar**
```bash
python app.py
```

La aplicación estará disponible en `http://localhost:5000`

## Configuración en Render

### Web Service

1. Crear nuevo Web Service conectado al repositorio
2. Configurar:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
3. Agregar variables de entorno

### Base de Datos PostgreSQL

1. Crear nueva base de datos PostgreSQL (plan Free)
2. Copiar Internal Database URL
3. Agregar como variable `DATABASE_URL` en el Web Service

## Bot de Telegram

### Comandos

- `/start` - Iniciar el bot y ver opciones

### Funcionalidades

- **Consultar Brecha**: Ver tasas actuales
- **Suscribirse**: Recibir notificaciones automáticas
- **Desuscribirse**: Dejar de recibir notificaciones

### Notificaciones Automáticas

- 8:00 AM (hora Venezuela)
- 2:00 PM (hora Venezuela)
- 10:00 PM (hora Venezuela)

### Alertas

El bot envía alertas cuando la brecha USDT vs Dólar BCV cambia más del 5%.

## API Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/prices` | Último registro de precios |
| GET | `/api/latest` | Último registro (alias) |
| GET | `/api/history` | Historial con filtros |
| POST | `/api/refresh` | Forzar actualización |

### Parámetros de `/api/history`

- `start`: Fecha inicio (ISO format)
- `end`: Fecha fin (ISO format)
- `limit`: Máximo de registros (default: 100)
- `offset`: Para paginación (default: 0)

## Estructura del Proyecto

```
brecha-cambiaria/
├── app.py                 # Aplicación principal
├── requirements.txt       # Dependencias
├── .env                   # Variables de entorno (no en git)
├── .gitignore
├── README.md
└── static/
    └── index.html         # Frontend
```

## Fuentes de Datos

- **BCV**: https://www.bcv.org.ve/ (web scraping)
- **Binance P2P**: API oficial de Binance

## Licencia

MIT License

## Autor

Ricardo Marin
