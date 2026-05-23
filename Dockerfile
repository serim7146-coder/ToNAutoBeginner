FROM python:3.12-slim

WORKDIR /app

# Node.jsインストール
RUN apt-get update && apt-get install -y curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# RUN pip install -r requirements_dev.txt

EXPOSE 5173

CMD ["bash"]


# ビルド
# docker build -t ton_auto_beginner .
# コンテナ
# docker run -it -p 5173:5173 -p 8080:8080 -v ${PWD}:/app --name ton_auto_beginner ton_auto_beginner /bin/bash
# htmlの起動
# python3 -m http.server 8080
