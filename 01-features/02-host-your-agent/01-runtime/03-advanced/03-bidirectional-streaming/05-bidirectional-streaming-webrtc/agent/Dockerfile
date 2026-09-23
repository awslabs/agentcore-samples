FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Create non-root user and transfer ownership
RUN useradd -r -m -u 1001 appuser && chown -R appuser /app
USER appuser

CMD ["python", "bot.py", "--host", "0.0.0.0", "--port", "8080"]
