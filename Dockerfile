FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 --create-home club && mkdir /data && chown club:club /data
COPY app ./app
COPY public ./public
USER club
EXPOSE 8097
CMD ["python", "-m", "app.server"]
