FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade 'pip>=26.2,<27' && python -m pip install --no-cache-dir -r requirements.txt
COPY agent.py ./
RUN useradd -m -u 1000 runtime
USER runtime
ENV CLAUDE_CODE_USE_BEDROCK=1
EXPOSE 8080
CMD ["python", "agent.py"]
