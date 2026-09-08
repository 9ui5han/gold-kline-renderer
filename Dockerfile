FROM python:3.12-slim

ARG DEBIAN_MIRROR=https://mirrors.aliyun.com

RUN for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
      if [ -f "$source_file" ]; then \
        sed -i \
          -e "s#https\?://deb.debian.org/debian-security#${DEBIAN_MIRROR}/debian-security#g" \
          -e "s#https\?://deb.debian.org/debian#${DEBIAN_MIRROR}/debian#g" \
          -e "s#https\?://security.debian.org/debian-security#${DEBIAN_MIRROR}/debian-security#g" \
          "$source_file"; \
      fi; \
    done \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY assets/photo ./assets/photo

ENV PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
