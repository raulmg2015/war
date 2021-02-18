from typing import Text, Any, Optional, Callable, Awaitable
from asyncio import Queue
from sanic.request import Request
from rasa.core.channels import InputChannel
from rasa.core.channels.channel import UserMessage, CollectingOutputChannel, QueueOutputChannel
from sanic import Blueprint, response
import inspect
from redis import Redis
import rasa
import asyncio

import os
import uuid
import json
import logging
import requests
import unidecode
from datetime import datetime

# REDIS
redis_host = os.environ.get("REDIS_HOSTNAME", "localhost")
redis_pass = os.environ.get("REDIS_PASSWORD", "")
redis = Redis(
    host=redis_host,
    password=redis_pass,
    decode_responses=True
)

# ENVS
connector_url = os.environ.get("CONNECTOR_URL", "")

# TOKENS
end_token = os.environ.get("END_TOKEN", "")
start_token = os.environ.get("START_TOKEN", "")

# FLAGS
mock = True if os.environ.get("MOCK", "") == "true" else False

data_ex = 3600

logger = logging.getLogger(__name__)


def strip_accents(string: str) -> str:
    return unidecode.unidecode(string)

def equals(string_1: str, string_2: str) -> bool:
    return strip_accents(string_1).upper().strip() == strip_accents(string_2).upper().strip()


class RestInput(InputChannel):
    @classmethod
    def name(cls):
        return "whatsapp"

    @staticmethod
    async def on_message_wrapper(
        on_new_message: Callable[[UserMessage], Awaitable[None]],
        text: Text,
        queue: Queue,
        sender_id: Text,
        input_channel: Text,
    ) -> None:
        collector = QueueOutputChannel(queue)
        message = UserMessage(text, collector, sender_id, input_channel=input_channel)
        await on_new_message(message)
        await queue.put("DONE")

    async def _extract_sender(self, req: Request) -> Optional[Text]:
        return req.json.get("sender", None)

    def _extract_message(self, req: Request) -> Optional[Text]:
        return req.json.get("message", None)

    def _extract_input_channel(self, req: Request) -> Text:
        return req.json.get("input_channel") or self.name()

    def stream_response(
        self,
        on_new_message: Callable[[UserMessage], Awaitable[None]],
        text: Text,
        sender_id: Text,
        input_channel: Text,
    ) -> Callable[[Any], Awaitable[None]]:
        async def stream(resp: Any) -> None:
            q: Queue
            q = Queue()
            task = asyncio.ensure_future(
                self.on_message_wrapper(
                    on_new_message, text, q, sender_id, input_channel
                )
            )
            result = None
            while True:
                result = await q.get()
                if result == "DONE":
                    break
                else:
                    await resp.write(json.dumps(result) + "\n")
            await task

        return stream

    def blueprint(self, on_new_message: Callable[[UserMessage], Awaitable[None]]):
        custom_webhook = Blueprint(
            "custom_webhook_{}".format(type(self).__name__),
            inspect.getmodule(self).__name__,
        )
        @custom_webhook.route("/", methods=["GET"])
        async def health(request: Request):
            return response.json({"status": "ok"})

        @custom_webhook.route("/webhook", methods=["POST"])
        async def receive(request: Request):
            data = request.json

            logging.info(data)
            cellphone = data.get("from", "")
            text_body = data.get("body", "")
            media_url = data.get("media_url", "")
            content_type = data.get("content_type", "")
            message_id = data.get("message_id", "")

            text_body = "" if text_body == "null" else text_body
            media_url = "" if media_url == "null" else media_url
            content_type = "" if content_type == "null" else content_type

            ####################################################################
            text = text_body
            sender_id = f"rasa:{cellphone}"
            ####################################################################
            # CHECK DAILY QUOTA
            ####################################################################

            if equals(text_body, end_token):
                text = "/end"
                if redis.get(sender_id) is None:
                    return response.json({"message": "Conversation ended but no conversation present"})

            elif equals(text_body, start_token):
                text = "/greet"

            elif redis.get(sender_id) is None:
                logging.debug("NEW CONVERSATION")
                text = "/greet"

            ####################################################################
            message_flag = redis.get(sender_id + ":message_flag")
            message_json = None

            if message_flag:
                logging.debug(">> MESSAGE FLAG")
                message_json = {
                    "body": text_body,
                    "from": cellphone,
                    "content_type": content_type,
                    "media_url": media_url
                }

            # logging.info("MESSAGE")
            # logging.info(json.dumps(message_json, indent=4))
            # redis.setex(sender_id+":message", data_ex, json.dumps(message_json))

            ####################################################################
            should_use_stream = rasa.utils.endpoints.bool_arg(
                request, "stream", default=False
            )
            input_channel = self._extract_input_channel(request)

            if should_use_stream:
                return response.stream(
                    self.stream_response(
                        on_new_message, text, sender_id, input_channel
                    ),
                    content_type="text/event-stream",
                )
            else:
                collector = CollectingOutputChannel()
                try:
                    await on_new_message(
                        UserMessage(text,
                                    collector,
                                    sender_id,
                                    input_channel=input_channel,
                                    metadata=message_json))
                except asyncio.CancelledError:
                    logger.error(
                        "Message handling timed out for "
                        "user message '{}'.".format(text)
                    )
                except Exception:
                    logger.exception(
                        "An exception occured while handling "
                        "user message '{}'.".format(text)
                    )

                ################################################################
                logging.info(collector.messages)

                if mock:
                    return response.json(collector.messages)

                ################################################################

                try:
                    for mess in collector.messages:
                        mess_dict = dict(body=mess.get("text"), to=cellphone, mediaUrl="", contentType="text")
                        r = requests.post(
                            connector_url,
                            json=mess_dict
                        )
                        logging.debug(r.text)
                        logging.debug(r.status_code)

                except requests.exceptions.ConnectionError as e:
                    logging.error("COULDN'T SEND MESSAGE TO CONNECTOR")
                    logging.error(e)

                ################################################################
                return response.json(collector.messages)

        return custom_webhook
