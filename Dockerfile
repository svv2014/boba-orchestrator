FROM python:3.11-slim

WORKDIR /app

# Create non-root user early so we can set ownership in one layer
RUN groupadd --system app && useradd --system --gid app app

# Copy dependency manifest first for better layer caching
COPY pyproject.toml .

# Install only runtime dependencies (no dev extras)
# python 3.11+ has tomllib built in
RUN python -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
print('\n'.join(data['project']['dependencies']))
" > /tmp/requirements-prod.txt && \
    pip install --no-cache-dir -r /tmp/requirements-prod.txt

# Copy source and install the package itself (deps already installed above)
COPY . .
RUN pip install --no-cache-dir --no-deps . && \
    chown -R app:app /app

USER app

ENTRYPOINT ["orchestrator"]
