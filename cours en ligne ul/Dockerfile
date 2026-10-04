FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCCAMPUS_DATABASE=/data/doccampus.sqlite3 \
    DOCCAMPUS_UPLOADS=/data/uploads

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py create_admin.py admin.html admin.js index.html script.js style.css ./
COPY assets ./assets

EXPOSE 8000
VOLUME ["/data"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
