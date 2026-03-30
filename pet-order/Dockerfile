FROM python:3.11-slim

WORKDIR /app

COPY pet-order/app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pet-order/app/app.py .

EXPOSE 8080

CMD ["python", "app.py"]