FROM public.ecr.aws/lambda/python:3.10

# Create larger temp directory and set environment variable
RUN mkdir -p /tmp/pip-tmp
ENV TMPDIR=/tmp/pip-tmp
ENV PIP_NO_CACHE_DIR=1

# Install dependencies with disk space optimizations
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -t .
    
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords'); nltk.download('wordnet'); nltk.download('averaged_perceptron_tagger'); nltk.download('omw-1.4')"

COPY app.py ./

CMD ["app.lambda_handler"]