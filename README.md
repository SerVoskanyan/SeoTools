# SEO-Анализатор (Fullstack)

Фронтенд (`index.html`) + Python-бэкенд (FastAPI) для технического SEO-аудита без ограничений браузерного CORS и публичных прокси.

## Структура проекта

| Файл | Назначение |
|------|------------|
| `main.py` | FastAPI API: загрузка страниц, SEO-анализ, SEO Score |
| `requirements.txt` | Зависимости Python |
| `index.html` | Dashboard (Tailwind, PDF, EmailJS) |
| `Dockerfile` | Контейнер для деплоя (порт **8000** внутри контейнера) |

## Локальный запуск (рекомендуется)

### 1. Бэкенд

```bash
cd "/Users/serikvoskanan/Desktop/Автоматизация/SEO Tools"
python3.11 -m venv venv   # в Docker и на Render — Python 3.11; локально лучше 3.11–3.12
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

> Порт **8000** на хосте не пересекается с вашими контейнерами (`8096`, `8091`, `8085`). Для Docker см. проброс `8092:8000` ниже.

Проверка:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Документация API: [http://localhost:8000/docs](http://localhost:8000/docs)

### 2. Фронтенд

Откройте `index.html` в браузере (двойной клик или Live Server).

При `hostname` = `localhost` / `127.0.0.1` фронтенд автоматически шлёт запросы на `http://localhost:8000`.

> **Совет:** если браузер блокирует запросы с `file://`, поднимите статику локально:
> `python3 -m http.server 5500` и откройте `http://localhost:5500/index.html`.

### 3. EmailJS

В начале `<script>` в `index.html` укажите свои ключи:

- `EMAILJS_PUBLIC_KEY`
- `EMAILJS_SERVICE_ID`
- `EMAILJS_TEMPLATE_ID`

В шаблоне EmailJS используйте переменные: `to_email`, `site_url`, `seo_score`, `score_label`, `report_summary_html`.

## Docker (локально, без конфликта портов)

У вас уже заняты маппинги `8096:8090`, `8091:8090`, `8085:80`. Этот сервис слушает **8000** внутри контейнера — пробросьте на свободный хост-порт, например **8092**:

```bash
docker build -t seo-analyzer-api .
docker run --rm -p 8092:8000 seo-analyzer-api
```

API будет на `http://localhost:8092`. Для фронтенда временно замените `API_BASE_URL` на `http://localhost:8092` или используйте prod URL после деплоя.

## Деплой бэкенда на Render.com (~2 минуты)

1. Залейте репозиторий на GitHub (нужны `main.py`, `requirements.txt`, `Dockerfile`).
2. [render.com](https://render.com) → **New** → **Web Service** → подключите репо.
3. **Environment:** Docker *(или Native: Build `pip install -r requirements.txt`, Start `uvicorn main:app --host 0.0.0.0 --port $PORT`)*.
4. **Instance type:** Free.
5. После деплоя скопируйте URL вида `https://seo-analyzer-xxxx.onrender.com`.
6. В `index.html` замените placeholder в `API_BASE_URL`:
   ```javascript
   : 'https://seo-analyzer-xxxx.onrender.com';
   ```
7. Опубликуйте `index.html` на **GitHub Pages** (ветка `gh-pages` или `/docs`).

Готово: статика на GitHub Pages, анализ — на Render с CORS `*`.

## Railway / другие PaaS

Аналогично: Docker-образ или `uvicorn main:app --host 0.0.0.0 --port $PORT`, затем URL в `API_BASE_URL`.

## Ограничения

Сайты с жёсткой anti-bot защитой (Cloudflare Turnstile, Qrator и т.д.) могут по-прежнему отдавать 403 с IP дата-центров. Бэкенд делает ретраи и ротацию User-Agent, но для 100% обхода может понадобиться residential proxy или headless-браузер (Playwright) — это следующий этап.

## Лицензия

Клиентский инструмент — используйте по договорённости с заказчиком.
