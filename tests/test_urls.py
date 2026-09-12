from services.collector.app.main import chat_url


class Entity:
    id = 123456789


def test_public_url():
    assert chat_url(Entity(), "example", 42) == "https://t.me/example/42"


def test_private_url():
    assert chat_url(Entity(), None, 42) == "https://t.me/c/123456789/42"
