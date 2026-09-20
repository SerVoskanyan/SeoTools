# Структура проекта SEO Tools

Fullstack SEO-аудит: **frontend/** (дашборд) + **backend/** (FastAPI). Деплой на **Vercel** (serverless API + static). Локально — **Docker Compose** или `start.sh`.

```
SEO Tools/
├── api/
│   └── index.py            # Точка входа Vercel Serverless → backend.main:app
├── backend/
│   ├── __init__.py
│   └── main.py             # FastAPI: CORS, SEO-анализ, POST /api/analyze, GET /api/health
├── frontend/
│   ├── index.html          # UI, запросы на относительный /api/analyze
│   └── index.css
├── vercel.json             # Маршруты /api/* → Python, остальное → static frontend
├── requirements.txt
├── Dockerfile              # uvicorn backend.main:app :8000
├── docker-compose.yml      # seo-tools-dev, env_file .env, volume hot reload
├── .gitignore              # .env, .env.*, __pycache__, venv, …
├── .env.example
├── nginx/                  # Legacy (опционально)
├── start.sh / start_docker.sh
└── README.md
```

## Локальный Docker

```bash
docker compose up --build
```

- API: http://localhost:8000/api/health  
- UI (статика с того же origin): http://localhost:8000/

## Vercel

- `frontend/**` — static build  
- `api/index.py` — `@vercel/python` для `/api/*`
