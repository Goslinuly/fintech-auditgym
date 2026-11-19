FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends make && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY auditgym ./auditgym
RUN pip install --no-cache-dir -e ".[dev]"
COPY . .
CMD ["make", "check"]
