# FROM public.ecr.aws/lambda/python:3.10

# COPY requirements.txt ./

# RUN python3.10 -m pip install -r requirements.txt -t .

# COPY app.py ./

# CMD ["app.lambda_handler"]


# Streaming Things
FROM public.ecr.aws/docker/library/python:3.12.0-slim-bullseye
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

COPY appie.py ./
ADD . .
RUN pip install -r requirements.txt

CMD ["python", "appie.py"]