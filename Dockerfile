# The MVP as one container: the React screens are built, then served by the FastAPI app.
# Cloud Run sets $PORT; locally:  docker run -p 8080:8080 -e DATABASE_URL=... -e SECRET_KEY=... <image>

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    STATIC_DIR=/srv/web/dist
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY onboarding/ onboarding/
COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini .
COPY --from=web /web/dist web/dist
RUN useradd --create-home --uid 10001 app && mkdir -p /srv/data && chown app /srv/data
USER app
EXPOSE 8080
# Apply migrations, then serve. Cloud Run runs at most one instance (see docs/DEPLOY.md), so
# migrations never race.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
