FROM --platform=linux/amd64 python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV OUTSTATION_HOST=outstation
ENV OUTSTATION_PORT=20000
ENV WEB_PORT=8080

EXPOSE 8080

CMD ["python", "app.py"]
