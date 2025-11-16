#!/bin/bash

# ---------------------------------------------
# 可配置参数（可通过命令行覆盖）
# ---------------------------------------------
DOCKER_IMAGE="${1:-python:3.11-slim}"
CONTAINER_NAME="${2:-cosight-runtime}"
HOST_PORT="${3:-7788}"
ENV_FILE="${4:-.env}"

echo "[INFO] 使用镜像: $DOCKER_IMAGE"
echo "[INFO] 容器名称: $CONTAINER_NAME"
echo "[INFO] 映射端口: $HOST_PORT:7788"
echo "[INFO] 环境变量文件: $ENV_FILE"

# 检查环境文件是否存在
if [ -f "$ENV_FILE" ]; then
    ENV_ARG="--env-file $ENV_FILE"
else
    ENV_ARG=""
    echo "[WARN] 未找到 $ENV_FILE，将沿用宿主机环境变量"
fi

# ---------------------------------------------
# 拉取镜像并清理旧容器
# ---------------------------------------------
echo "[INFO] 拉取镜像 $DOCKER_IMAGE..."
docker pull $DOCKER_IMAGE
if [ $? -ne 0 ]; then
    echo "[ERROR] docker pull 失败"
    exit 1
fi

echo "[INFO] 清理旧容器..."
docker stop $CONTAINER_NAME >/dev/null 2>&1
docker rm $CONTAINER_NAME >/dev/null 2>&1

# ---------------------------------------------
# 以挂载方式启动容器：
# - 映射当前工程到 /app
# - 安装依赖并启动 FastAPI
# ---------------------------------------------
echo "[INFO] 启动容器..."
docker run \
    -d \
    --name $CONTAINER_NAME \
    -p $HOST_PORT:7788 \
    -v "$(pwd)":/app \
    -w /app \
    $ENV_ARG \
    $DOCKER_IMAGE \
    bash -lc "pip install --no-cache-dir -r requirements.txt && python cosight_server/deep_research/main.py"

if [ $? -ne 0 ]; then
    echo "[ERROR] 容器启动失败"
    exit 1
fi

echo ""
echo "[INFO] Co-Sight 已在 Docker 中启动，可访问 http://localhost:$HOST_PORT/cosight/"
echo "[INFO] 若需关闭，执行: docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME"
echo ""
