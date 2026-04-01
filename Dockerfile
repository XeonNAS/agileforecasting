FROM python:3.12-slim

# Chromium is required by Kaleido for PNG/SVG chart export.
# Skip this block if you don't need chart downloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python package (separate layer so src/ changes don't invalidate pip cache)
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Copy the Streamlit app and config
COPY streamlit_app/ streamlit_app/
COPY .streamlit/ .streamlit/

# Run as non-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Tell Kaleido where Chromium is
ENV BROWSER_PATH=/usr/bin/chromium

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "streamlit_app/app.py"]
