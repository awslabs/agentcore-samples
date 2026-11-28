FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade 'pip>=26.2,<27' && python -m pip install --no-cache-dir -r requirements.txt
COPY fmkb_gateway_strands.py ./
RUN useradd -m -u 1000 runtime
USER runtime
EXPOSE 8080
CMD ["python", "fmkb_gateway_strands.py"]
