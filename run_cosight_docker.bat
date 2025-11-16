@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

REM ---------------------------------------------
REM 可配置参数（可通过命令行覆盖）
REM ---------------------------------------------
set "DOCKER_IMAGE=%~1"
if "%DOCKER_IMAGE%"=="" set "DOCKER_IMAGE=python:3.11-slim"

set "CONTAINER_NAME=%~2"
if "%CONTAINER_NAME%"=="" set "CONTAINER_NAME=cosight-runtime"

set "HOST_PORT=%~3"
if "%HOST_PORT%"=="" set "HOST_PORT=7788"

set "ENV_FILE=%~4"
if "%ENV_FILE%"=="" set "ENV_FILE=.env"

echo [INFO] 使用镜像: %DOCKER_IMAGE%
echo [INFO] 容器名称: %CONTAINER_NAME%
echo [INFO] 映射端口: %HOST_PORT%:7788
echo [INFO] 环境变量文件: %ENV_FILE%

if exist "%ENV_FILE%" (
    set "ENV_ARG=--env-file %ENV_FILE%"
) else (
    set "ENV_ARG="
    echo [WARN] 未找到 %ENV_FILE%，将沿用宿主机环境变量
)

REM ---------------------------------------------
REM 拉取镜像并清理旧容器
REM ---------------------------------------------
docker pull %DOCKER_IMAGE%
if errorlevel 1 (
    echo [ERROR] docker pull 失败
    exit /b 1
)

docker stop %CONTAINER_NAME% >nul 2>&1
docker rm %CONTAINER_NAME% >nul 2>&1

REM ---------------------------------------------
REM 以挂载方式启动容器：
REM - 映射当前工程到 /app
REM - 安装依赖并启动 FastAPI
REM ---------------------------------------------
docker run ^
    -d ^
    --name %CONTAINER_NAME% ^
    -p %HOST_PORT%:7788 ^
    -v "%cd%":/app ^
    -w /app ^
    %ENV_ARG% ^
    %DOCKER_IMAGE% ^
    bash -lc "pip install --no-cache-dir -r requirements.txt && python cosight_server/deep_research/main.py"

if errorlevel 1 (
    echo [ERROR] 容器启动失败
    exit /b 1
)

echo.
echo [INFO] Co-Sight 已在 Docker 中启动，可访问 http://localhost:%HOST_PORT%/cosight/
echo [INFO] 若需关闭，执行: docker stop %CONTAINER_NAME% && docker rm %CONTAINER_NAME%
echo.

endlocal

