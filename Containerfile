FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# App code (collector + dashboard + config). Exclude data/ via .containerignore
COPY collector/ ./collector/
COPY dashboard/ ./dashboard/
COPY config.yaml run.sh ./
RUN chmod +x run.sh && mkdir -p data
EXPOSE 8282
ENTRYPOINT ["./run.sh"]
