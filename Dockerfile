# Pin to a specific digest so builds are reproducible and immune to tag mutation.
# To update: docker manifest inspect python:3.12-slim, then replace the digest below.
FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

# Chromium is required by Kaleido for PNG/SVG chart export.
# Skip this block if you don't need chart downloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned runtime dependencies first — separate layer so the slow
# download step is cached as long as requirements.lock hasn't changed.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Install the package itself (no dep resolution — everything is already installed)
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

# Copy the Streamlit app and config
COPY streamlit_app/ streamlit_app/
COPY .streamlit/ .streamlit/

# Run as non-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Tell Kaleido where Chromium is
ENV BROWSER_PATH=/usr/bin/chromium
# Bind to all interfaces inside the container so the mapped port is reachable
# from the host. config.toml leaves address unset (defaults to localhost) so
# this env var only takes effect in the container.
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "streamlit_app/app.py"]
