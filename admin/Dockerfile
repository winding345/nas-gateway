# ══════════════════════════════════════════════════════════════
#  用户管理页 + 配置生成器
#
#  只装两个依赖：
#    · argon2-cffi  —— 生成 Authelia 兼容的 argon2id 密码哈希
#    · PyYAML       —— 读写 users_database.yml / 生成配置
#  Web 服务本身用 Python 标准库（http.server），不引入框架
# ══════════════════════════════════════════════════════════════
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
# 把代码也打进镜像，这样镜像可以独立运行（不依赖 compose 的卷挂载）
# docker-compose.yml 里仍然挂载 ./admin:/app，方便改代码后重启即生效
COPY *.py /app/
# ⚠️ 必须显式放开读权限：compose 里用 user: "${PUID}:${PGID}" 以非 root 身份运行，
#    而 git 不跟踪文件权限（只跟踪可执行位），从某些环境 checkout 出来的源码可能是
#    0600，COPY 进镜像后仍是 0600 且属 root → 容器里 python 会报
#    "can't open file '/app/app.py': [Errno 13] Permission denied"。
RUN chmod 0644 /app/*.py

EXPOSE 9092

CMD ["python", "app.py"]
