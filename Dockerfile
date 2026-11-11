FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    TZ=Asia/Shanghai

WORKDIR /app

# gosu 用于从 root 安全降权；rsync 用于热数据备份
RUN apt-get update && apt-get install -y --no-install-recommends \
    rsync \
    gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 app \
    && useradd -u 1000 -g app -m -s /bin/sh app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY web/ ./web/
COPY config/ ./config/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p data logs

EXPOSE 8765

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
