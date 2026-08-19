FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools==84.0.0
RUN addgroup --system telnexa && adduser --system --ingroup telnexa telnexa
COPY billing/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip uninstall -y pip setuptools
COPY --chown=telnexa:telnexa billing /app/billing
USER telnexa
EXPOSE 8000
CMD ["uvicorn","billing.app:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
