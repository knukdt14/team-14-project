FROM python:3.12-slim

WORKDIR /app

# HEALTHCHECK에 필요한 curl만 설치하고 apt 캐시는 남기지 않는다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# 컨테이너를 root로 실행하지 않도록 전용 사용자로 전환한다.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:7860/_stcore/health || exit 1

CMD ["streamlit", "run", "src/app.py", "--server.port=7860", "--server.address=0.0.0.0"]
