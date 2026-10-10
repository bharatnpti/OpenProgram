FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.2
COPY pyproject.toml uv.lock ./
# Install exactly what uv.lock pins, runtime dependencies only. `pip install .`
# re-resolved the >= ranges on every build, so containers ran newer libraries
# than the tests (DBOS 3.2 against 2.24 once, which changed schedule semantics).
# --no-deps: the export already lists every package the lock installs, so pip
# adds none of its own -- not a package the lock leaves out by its marker
# (langchain-google-genai, which deepagents lists but never imports).
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY backend ./backend
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONPATH=/app/backend
CMD ["uvicorn", "api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
