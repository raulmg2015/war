FROM jaimeteb/rasa-es:1.10.1 AS base
WORKDIR /app

RUN pip install --upgrade pip && \
    pip install requests fuzzywuzzy unidecode zeep pydub && \
    apt-get update && \
    apt-get install ffmpeg libavcodec-extra -y

COPY . .
RUN chmod a+x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]

FROM base AS train
RUN rasa train --force
