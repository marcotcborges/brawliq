FROM python:3.11-slim

WORKDIR /app

# install deps first — this layer is cached unless pyproject.toml changes
COPY pyproject.toml ./
RUN pip install --no-cache-dir . --quiet

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
