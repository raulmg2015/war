#!/bin/bash

if [ "$DEBUG" = "1" ]
then
    echo "**** DEBUG MODE ****"
    rasa run actions --debug & \
    rasa run --port 5005 --enable-api --debug
else
    caddy reverse-proxy --from :5005 --to 127.0.0.1:6006 & \
    rasa run actions -v & \
    rasa run --port 6006 --enable-api -v --log-file rasa.log
fi

exec $@
