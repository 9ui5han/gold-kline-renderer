#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

CONTAINER_NAME="gold-kline-renderer"
IMAGE_NAME="gold-python-render"
HOST_PORT="18100"
CONTAINER_PORT="8000"
ENV_FILE="$ROOT_DIR/.env"
DATA_DIR_HOST="$ROOT_DIR/data"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "错误：缺少 .env 文件：$ENV_FILE"
  echo "请先复制 .env.example 为 .env，并填入实际密钥。"
  exit 1
fi

if ! grep -Eq '^RENDER_SERVICE_TOKEN=.+$' "$ENV_FILE"; then
  echo "错误：.env 中缺少有效的 RENDER_SERVICE_TOKEN"
  exit 1
fi

mkdir -p "$DATA_DIR_HOST"

echo "[1/6] 当前代码版本"
git branch --show-current
git log -1 --oneline

echo "[2/6] 重新构建 Docker 镜像（不使用旧缓存）"
docker build --no-cache -t "$IMAGE_NAME" "$ROOT_DIR"

echo "[3/6] 验证镜像包含 TOOL-09 路由"
docker run --rm "$IMAGE_NAME" \
  sh -c "grep -q '/v1/tool-09/segments/submit' /app/app/segment_renderer.py"

echo "[4/6] 删除旧容器"
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  docker rm -f "$CONTAINER_NAME"
fi

echo "[5/6] 启动新容器"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e PORT="$CONTAINER_PORT" \
  -e DATA_DIR="/data/gold-video" \
  -e PUBLIC_BASE_URL="http://192.168.1.200:8100" \
  -p "127.0.0.1:${HOST_PORT}:${CONTAINER_PORT}" \
  -v "${DATA_DIR_HOST}:/data" \
  "$IMAGE_NAME"

echo "等待服务启动..."
sleep 5

echo "[6/6] 验证服务和 TOOL-09 路由"
curl --fail --silent "http://127.0.0.1:${HOST_PORT}/health" >/dev/null
curl --fail --silent "http://127.0.0.1:${HOST_PORT}/openapi.json" \
  | grep -q '/v1/tool-09/segments/submit'

echo "启动成功：TOOL-09 路由已确认存在。"
echo "本机地址：http://127.0.0.1:${HOST_PORT}"
echo "Caddy 地址：http://192.168.1.200:8100"
echo "数据目录：${DATA_DIR_HOST}"
