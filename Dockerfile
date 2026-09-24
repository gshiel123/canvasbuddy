FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Dublin

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY canvas_buddy/ ./canvas_buddy/

RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "-m", "canvas_buddy.run", "watch"]
