FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./server/app
COPY run.py ./server/run.py
ENV DB_PATH=/data/push/server.db PUSH_STORAGE_DIR=/data/push
VOLUME ["/data/push"]
EXPOSE 8080
CMD ["python", "-m", "server.run"]
