FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app/backend
CMD ["uvicorn", "api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
