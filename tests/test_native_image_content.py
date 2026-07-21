"""Native image pipeline: attached images reach the LLM request as image content.

Chat Completions (AI Platform) -> {type:image_url, image_url:{url}};
Responses (Copilot) -> {type:input_image, image_url:<data-uri>}.
"""
from src.efp_runtime.llm.openai import (
    provider_request_to_openai_chat,
    provider_request_to_openai_responses,
)
from src.efp_runtime.llm.request import (
    ProviderRequest,
    RequestAttachment,
    RequestMessage,
    RequestMessagePart,
)
from src.efp_runtime.runtime.agent import AgentRuntime, _mime_from_data_uri
from src.efp_runtime.session.models import MessagePartType

DATA = "data:image/png;base64,iVBORw0KGgo="


def _img_req():
    att = RequestAttachment(attachment_id="a1", mime_type="image/png", url=DATA)
    msg = RequestMessage(
        role="user",
        parts=[
            RequestMessagePart(type="text", text="hi"),
            RequestMessagePart(type="attachment", attachment=att),
        ],
    )
    return ProviderRequest(messages=[msg], tools=[])


def _text_req():
    msg = RequestMessage(role="user", parts=[RequestMessagePart(type="text", text="hi")])
    return ProviderRequest(messages=[msg], tools=[])


def test_chat_emits_image_url_list():
    content = provider_request_to_openai_chat(_img_req(), model="m")["messages"][-1]["content"]
    assert isinstance(content, list)
    assert {"type": "text", "text": "hi"} in content
    assert {"type": "image_url", "image_url": {"url": DATA}} in content


def test_chat_text_only_stays_a_string():
    # No images -> plain string content, unchanged from before.
    content = provider_request_to_openai_chat(_text_req(), model="m")["messages"][-1]["content"]
    assert content == "hi"


def test_responses_emits_input_image():
    items = provider_request_to_openai_responses(_img_req(), model="m")["input"][-1]["content"]
    assert {"type": "input_image", "image_url": DATA} in items


def test_mime_from_data_uri():
    assert _mime_from_data_uri("data:image/jpeg;base64,x") == "image/jpeg"
    assert _mime_from_data_uri("data:image/webp,x") == "image/webp"
    assert _mime_from_data_uri("plain") == "image/png"


def test_merge_attached_images_injects_attachment_parts():
    # self is unused by the method.
    parts = AgentRuntime._merge_attached_images(None, None, "look at this", [DATA, "  "])
    assert parts is not None
    assert parts[0].type is MessagePartType.TEXT and parts[0].text == "look at this"
    images = [p for p in parts if p.type is MessagePartType.ATTACHMENT]
    assert len(images) == 1  # blank entries skipped
    assert images[0].attachment.url == DATA
    assert images[0].attachment.mime_type == "image/png"


def test_merge_no_images_returns_input_unchanged():
    assert AgentRuntime._merge_attached_images(None, None, "hi", None) is None
    sentinel = ["existing"]
    assert AgentRuntime._merge_attached_images(None, sentinel, "hi", []) is sentinel
