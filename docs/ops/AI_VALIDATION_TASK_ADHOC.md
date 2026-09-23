# Ad-hoc Cursor Task subagent validation
#
# Parent agent: for each mode A/B/C, spawn a Task with the matching model from
# config/ai_validators.yaml. Pass ONLY BlindItem fields (text + nonce). Parse JSON,
# POST to /validation/results with X-Validator-Token, then consensus/recompute.
#
# Do not pass score/tier/community/prior labels. Do not use third-party LLM APIs.
# Prefer scripts via: python -m services.validator.app.orchestrator --backend fake|sdk
