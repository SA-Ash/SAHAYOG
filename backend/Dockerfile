FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libzbar0 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend .
COPY mock_services /workspace/mock_services
RUN useradd -m engine && mkdir -p /workspace/data/uploads /workspace/data/mock && chown -R engine:engine /workspace
USER engine
ENV PYTHONPATH=/workspace:/workspace/backend
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
