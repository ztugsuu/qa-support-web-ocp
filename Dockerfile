# 1. Base image
FROM python:3.9-slim

# 2. Шаардлагатай package-ууд суулгах
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl tar gzip && \
    curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz && \
    tar -xzf openshift-client-linux.tar.gz -C /usr/local/bin oc kubectl && \
    chmod +x /usr/local/bin/oc /usr/local/bin/kubectl && \
    rm -f openshift-client-linux.tar.gz && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. App folder
WORKDIR /app

# 4. requirements install (Cache-ийг үр дүнтэй ашиглах)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Source copy
COPY app.py .
COPY rollout.py .
COPY allowlist.py .
COPY templates/ ./templates/
COPY static/ ./static/

# 6. Port
EXPOSE 5000

# 7. Run app (gunicorn-оор ажиллуулах)
# requirements.txt дотор 'gunicorn' орсон эсэхийг шалгаарай
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "120", "app:app"]
