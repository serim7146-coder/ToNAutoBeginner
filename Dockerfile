FROM python:3.12-slim

RUN apt-get update && apt-get install -y git

WORKDIR /app

RUN git clone https://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@github.com/serim7146-coder/ToNAutoBeginner.git /app
