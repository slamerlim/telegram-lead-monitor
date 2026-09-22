from shared.decisions import decision_from_result


class _R:
    def __init__(self, tier: str):
        self.tier = tier


def test_decision_mapping():
    assert decision_from_result(_R("HIGH")) == "POSITIVE"
    assert decision_from_result(_R("MEDIUM")) == "AMBIGUOUS"
    assert decision_from_result(_R("LOW")) == "NEGATIVE"
