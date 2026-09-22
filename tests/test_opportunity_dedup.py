from shared.opportunity import normalize_message_text, opportunity_key, text_hash


def test_text_hash_normalizes_whitespace():
    a = text_hash("hello   world\n\n")
    b = text_hash("hello world")
    assert a == b
    assert len(a) == 64


def test_opportunity_key_is_community_scoped():
    text = "Looking to buy a trading bot. Budget $3000."
    k1 = opportunity_key(10, text)
    k2 = opportunity_key(11, text)
    k1b = opportunity_key(10, "Looking to buy a trading bot. Budget $3000.")
    assert k1 != k2
    assert k1 == k1b
    assert k1.startswith("10:")
    assert normalize_message_text(" a \n b ") == "a b"
