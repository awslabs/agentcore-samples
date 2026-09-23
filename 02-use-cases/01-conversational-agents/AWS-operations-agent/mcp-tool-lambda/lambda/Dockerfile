FROM public.ecr.aws/lambda/python:3.11

# Set environment variable for python_repl to use writable directory
ENV PYTHON_REPL_PERSISTENCE_DIR=/tmp/repl_state

# Copy requirements first for better caching
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all Python files (including optimized system prompt)
COPY *.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["mcp-tool-handler.lambda_handler"]
