"""Redis stream names for commercial AI + discovery planes."""

COMMERCIAL_AI_REVIEW_STREAM = "telegram:commercial_ai_review"
COMMERCIAL_AI_REVIEW_DLQ = "telegram:commercial_ai_review:dlq"
COMMERCIAL_AI_GROUP = "commercial-ai-workers"

COMMERCIAL_DISCOVERY_STREAM = "telegram:commercial_discovery"
COMMERCIAL_DISCOVERY_DLQ = "telegram:commercial_discovery:dlq"
COMMERCIAL_DISCOVERY_GROUP = "commercial-discovery-workers"

COMMERCIAL_EPISODE_SHADOW_STREAM = "telegram:commercial_episode_shadow"
COMMERCIAL_EPISODE_SHADOW_DLQ = "telegram:commercial_episode_shadow:dlq"
COMMERCIAL_EPISODE_SHADOW_GROUP = "commercial-episode-shadow-workers"
