# Prism — semantic PR review, served as a web app.
# Uses git at runtime to snapshot/clone the repos it reviews, so the image includes git + tar.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git tar ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Render provides $PORT; serve.py reads it and binds 0.0.0.0.
ENV PORT=10000 \
    PRISM_CACHE=/tmp/prism-cache
EXPOSE 10000

CMD ["python3", "serve.py"]
