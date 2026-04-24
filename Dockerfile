FROM python:3.11-slim

# System deps for pandas/numpy (used by MarketMind when data is supplied).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements (minimal - most of the code uses stdlib only).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy all source code.
COPY . /app

# Ensure state directories exist and are writable.
RUN mkdir -p /app/NewsMind/state && chmod -R a+rwx /app/NewsMind/state

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV POLL_INTERVAL_SEC=60

# Run the live loop.
CMD ["python", "/app/main.py"]
