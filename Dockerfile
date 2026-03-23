FROM python:3.12-slim
RUN apt-get update && apt-get install -y \
    libcairo2 libcairo2-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 libgdk-pixbuf-xlib-2.0-0 libffi-dev shared-mime-info \
    fonts-dejavu fonts-liberation fonts-noto \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
