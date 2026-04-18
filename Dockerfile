FROM python:3.11-slim

WORKDIR /app

# Install system deps + k6 in one layer
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://dl.k6.io/key.gpg | gpg --dearmor -o /usr/share/keyrings/k6-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
        > /etc/apt/sources.list.d/k6.list && \
    apt-get update && apt-get install -y --no-install-recommends k6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runtime dirs — created here so volumes mount cleanly
RUN mkdir -p logs scripts reports checkpoints

# Ensure .perf policy layer is present (mounted volume will override at runtime)
RUN mkdir -p .perf/rules .perf/profiles .perf/baselines

# Default port — overridden per-service in docker-compose / k8s
EXPOSE 5000 5001 5002

# Default entrypoint — overridden by docker-compose command:
CMD ["python", "rca_agent.py"]
