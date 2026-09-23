FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

RUN pip install --upgrade pip

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    git \
    && rm -rf /var/lib/apt/lists/*

ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.1.7

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY server.py ./

ENV HOST="0.0.0.0"
ENV PORT=8080

EXPOSE 8080

CMD ["python", "server.py"]
