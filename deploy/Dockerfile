FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY underground_crm/ underground_crm/
COPY underground_email/ underground_email/
COPY underground_payments/ underground_payments/
COPY manage.py deploy/wsgi.py ./

RUN pip install --no-cache-dir gunicorn && \
    pip install --no-cache-dir -e .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
