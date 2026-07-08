FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libmupdf-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# entrypoint waits for postgres and runs migrations, then execs CMD
RUN chmod +x /app/docker/entrypoint.sh
ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Default CMD — overridden by docker-compose per environment
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
