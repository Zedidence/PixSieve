FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Pillow and OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libgomp1 \
    libjpeg62-turbo \
    libpng16-16 \
    libwebp7 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 pixsieve
USER pixsieve

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/ping').read()" || exit 1

# Run PixSieve without opening a browser (headless server mode)
CMD ["python", "-m", "pixsieve.app", "--no-browser", "--port", "5000"]
