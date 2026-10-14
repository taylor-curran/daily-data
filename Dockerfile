FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
CMD python -m app.seed && uvicorn app.server:app --host 0.0.0.0 --port $PORT
