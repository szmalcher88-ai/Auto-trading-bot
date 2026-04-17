FROM python:3.11-slim

WORKDIR /app

# System dependencies required for python-binance and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Prisma schema and generate client (before copying full source
# so this layer is cached as long as schema.prisma hasn't changed)
COPY prisma/ prisma/
RUN python -m prisma generate

# Copy application source
COPY . .

# Fix Windows CRLF line endings on entrypoint and ensure it is executable
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

# Runtime data directory — host bind-mounts override these paths;
# they exist here so the image is self-contained for local runs.
RUN mkdir -p /app/cache

EXPOSE 8080

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
