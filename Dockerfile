FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/data WATCH_INBOX=1 HOST=0.0.0.0 PORT=8000
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "serve.py"]
