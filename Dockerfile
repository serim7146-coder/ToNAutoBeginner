FROM python:3.12-slim

RUN apt-get update && apt-get install -y git curl

# Node.jsインストール
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

RUN git clone https://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@github.com/serim7146-coder/ToNAutoBeginner.git /app

WORKDIR /app

RUN pip install -r requirements_dev.txt

EXPOSE 5173

CMD ["bash"]


# ビルド
# docker build -t tonautobeginner.
# コンテナ
# docker run -it -p 8080:8080 --name tonautobeginner tonautobeginner /bin/bash
# htmlの起動
# python3 -m http.server 8080