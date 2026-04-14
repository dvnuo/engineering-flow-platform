from src.agents.core import _inject_attached_images_into_last_user_message


def test_inject_attached_images_into_last_user_message_adds_two_images():
    messages = [{"role": "user", "content": "compare these"}]
    attached_images = ["data:image/png;base64,AAA", "data:image/png;base64,BBB"]

    added_count = _inject_attached_images_into_last_user_message(
        messages=messages,
        attached_images=attached_images,
        max_prompt_images=2,
    )

    assert added_count == 2
    assert isinstance(messages[0]["content"], list)

    text_blocks = [b for b in messages[0]["content"] if isinstance(b, dict) and b.get("type") == "input_text"]
    image_blocks = [b for b in messages[0]["content"] if isinstance(b, dict) and b.get("type") == "input_image"]

    assert len(text_blocks) == 1
    assert len(image_blocks) == 2
    assert image_blocks[0]["image_url"] == "data:image/png;base64,AAA"
    assert image_blocks[1]["image_url"] == "data:image/png;base64,BBB"


def test_inject_attached_images_respects_existing_image_blocks_and_remaining_capacity():
    messages = [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "compare"},
            {"type": "input_image", "image_url": "data:image/png;base64,EXISTING"},
        ],
    }]
    attached_images = ["data:image/png;base64,AAA", "data:image/png;base64,BBB"]

    added_count = _inject_attached_images_into_last_user_message(
        messages=messages,
        attached_images=attached_images,
        max_prompt_images=2,
    )

    assert added_count == 1
    image_blocks = [b for b in messages[0]["content"] if isinstance(b, dict) and b.get("type") == "input_image"]
    assert len(image_blocks) == 2
    assert image_blocks[1]["image_url"] == "data:image/png;base64,AAA"
