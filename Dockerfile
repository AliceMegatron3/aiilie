FROM python:3.10-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 统一依赖入口（Batch 0）：安装 uv，并由 uv.lock 生成严格清单安装，
# 与 CI（uv sync --frozen）保持一致；不再依赖未锁定、易漂移的 requirements.txt。
# 先复制锁文件与项目定义以获得构建缓存分层。
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --python 3.10

# 仅当 requirements.txt 与锁文件不一致时保留为参考资料（不用于安装）
COPY requirements.txt ./requirements.txt

# 复制所有源代码（构建上下文已由 .dockerignore 剔除密钥/data 等敏感项）
COPY . .

# 阶段A：非 root 执行。data 目录挂载到 /app/data，统一在此持久化。
# 容器内 get_app_data_dir 在 Linux 下优先读 APPDATA，防止写入 /root/home 导致数据丢失。
ENV APPDATA=/app/data
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app

# 暴露 FastAPI 默认端口
EXPOSE 8000

USER appuser

# 运行服务（使用 uv sync 生成的 .venv 解释器，与锁文件锁定的一致）
CMD [".venv/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
