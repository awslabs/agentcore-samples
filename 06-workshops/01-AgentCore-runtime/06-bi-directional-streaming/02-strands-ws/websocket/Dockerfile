FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

RUN pip install --upgrade pip

# Install system dependencies for PyAudio and other packages
RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    gcc \
    g++ \
    make \
    libasound2-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set a fallback version for setuptools-scm (in case git metadata is missing)
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.1.7

# Copy requirements and install remaining dependencies
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy application files
COPY server.py ./
COPY agent.py ./

# Set environment variables
ENV HOST="0.0.0.0"
ENV PORT=8080

# Expose port
EXPOSE 8080

# Run application directly
CMD ["python", "server.py"]
