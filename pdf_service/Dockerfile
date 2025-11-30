FROM python:3.10-slim

# Install the heavy graphics libraries required by WeasyPrint
# Fixed package name: libgdk-pixbuf-2.0-0 (added hyphen)
RUN apt-get update && apt-get install -y \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python libs
RUN pip install --no-cache-dir fastapi uvicorn weasyprint

COPY . .

EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]