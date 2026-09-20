# SEO Analyzer & Audit Tool (Fullstack)

Автоматизированный инструмент для технического SEO-анализа и аудита сайтов. Построен на базе FastAPI (Serverless Backend) и чистого JS/Tailwind (Frontend).

🔗 **Live Demo (Production):** https://seo-tools-servoskanyan.vercel.app

## 🚀 Возможности

- Полный технический SEO-анализ страниц (мета-теги, заголовки, изображения, ссылки).
- Расчет итогового SEO Score и генерация рекомендаций.
- Адаптирован под деплой на Vercel Serverless.
- Поддержка локального запуска в изолированном Docker-контейнере.

## 🛠 Технологический стек

- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **Frontend:** HTML5, Tailwind CSS, JavaScript (ES6+)
- **DevOps:** Docker, Docker Compose, Vercel Serverless

## 💻 Локальный запуск (Docker)

1. Клонируйте репозиторий:

```bash
git clone https://github.com/SerVoskanyan/SeoTools.git
cd SeoTools
```

2. Запустите проект через Docker Compose:

```bash
docker compose up --build -d
```

3. Откройте сервис в браузере:

- Приложение: http://localhost:8000
- Swagger API Docs: http://localhost:8000/docs

**Разработал:** SerVoskanyan
