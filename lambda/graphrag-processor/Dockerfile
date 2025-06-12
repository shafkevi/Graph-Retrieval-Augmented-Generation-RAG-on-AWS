FROM public.ecr.aws/lambda/python:3.10

# Create larger temp directory and set environment variable
RUN mkdir -p /tmp/pip-tmp
ENV TMPDIR=/tmp/pip-tmp
ENV PIP_NO_CACHE_DIR=1

# Install dependencies with disk space optimizations
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -t .
    
COPY app.py ./

CMD ["app.lambda_handler"]