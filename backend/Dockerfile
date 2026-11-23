FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend /app
RUN useradd --uid 10001 --create-home researcher && mkdir -p /app/data /input && chown -R researcher:researcher /app
COPY --chown=researcher:researcher --chmod=0400 ["FINAL_DATA_RECODE_RD2_Validate_AI.xlsx", "/input/study.xlsx"]
USER researcher
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py import_workbook && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile -"]

