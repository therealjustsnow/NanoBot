FROM python:3.11-slim

WORKDIR /app

# Install deps first — layer cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Ensure runtime dirs exist inside the image
RUN mkdir -p data logs

CMD ["python", "main.py"]
