FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
RUN pip install --no-cache-dir --no-deps . && useradd --create-home --uid 10001 vpn
COPY alembic.ini ./
COPY migrations ./migrations
USER vpn
CMD ["python", "-m", "app.main"]
