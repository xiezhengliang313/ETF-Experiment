# ============================================================
# 一个镜像 = 前端构建产物 + FastAPI 后端（单服务，最省钱）
# 方案A：Render 免费实例直接用这个文件；方案B：docker compose 也会调它
# ============================================================

# ---------- 阶段1：编译前端（Node 不进最终镜像）----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 阶段2：后端运行环境 ----------
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
# 前端产物叠进来（后端托管它，见 api_server.py 末尾的静态文件段）
COPY --from=web /web/dist ./frontend/dist

EXPOSE 8000
# Render 用 $PORT 指定端口，本地/服务器没这个变量就退回 8000，两边都能用
CMD ["sh", "-c", "uvicorn api_server:app --host 0.0.0.0 --port ${PORT:-8000}"]
