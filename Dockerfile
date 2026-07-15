FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . || pip install --no-cache-dir \
      fastapi uvicorn[standard] sqlalchemy[asyncio] asyncpg alembic \
      pydantic pydantic-settings structlog

COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
