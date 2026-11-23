FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade 'pip>=26.2,<27' && python -m pip install --no-cache-dir -r requirements.txt
COPY server.py s2s_events.py s2s_session_manager.py ./
ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
RUN useradd -r -m -u 1001 appuser
USER appuser
CMD ["python", "server.py"]
