FROM public.ecr.aws/lambda/python:3.10
# COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter


COPY requirements.txt ./

RUN python3.10 -m pip install -r requirements.txt -t .

COPY app.py ./

CMD ["app.lambda_handler"]

# CMD ["python", "app.py"]