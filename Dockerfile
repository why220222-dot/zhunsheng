FROM python:3.12-slim

WORKDIR /app
COPY . /app

ENV HOST=0.0.0.0 \
    PORT=7860 \
    SEED_DEMO=1 \
    DEMO_PEOPLE=2 \
    GENESIS_BASE_URL=https://token.moi.matrixorigin.cn/v1 \
    GENESIS_MODEL=deepseek-v4.1-flash

EXPOSE 7860

# 只用标准库，无需 pip install
CMD ["python", "server.py"]
