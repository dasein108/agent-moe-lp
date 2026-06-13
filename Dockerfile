# Merchant Moe (Mantle) Farming Bot Dockerfile
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies including build tools for numpy/pandas
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    libblas-dev \
    python3-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy only the dependency files first (for better layer caching)
COPY requirements.txt ./

# Install Python dependencies from requirements.txt with verbose output
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --verbose -r requirements.txt && \
    python -c "import numpy; import pandas; import scipy; print('✅ All quantitative packages installed successfully')"

# Create non-root user for security first
RUN groupadd -r moebot && useradd -r -g moebot -d /app -s /bin/bash moebot

# Create all required directories for persistence and logging
RUN mkdir -p /app/data/calibration /app/logs && \
    chmod 777 /app/data && \
    chmod 777 /app/data/calibration && \
    chmod 777 /app/logs

# Set ownership and create directories with proper permissions
RUN chown -R moebot:moebot /app && \
    chmod 755 /app

# Switch to non-root user
USER moebot

# Expose port (if needed for future web interface)
EXPOSE 8080

# Default command (can be overridden in docker-compose)
CMD ["python3", "-m", "moe_mantle_bot.farm_bot", "--help"]