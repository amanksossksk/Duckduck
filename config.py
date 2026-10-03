import os

class Config:
    # API Keys (add your own)
    API_KEYS = set(os.environ.get("API_KEYS", "test-key-123").split(","))
    
    # Token harvester endpoint
    TOKEN_HARVESTER_URL = os.environ.get("TOKEN_HARVESTER_URL", "http://192.168.0.11:5000/token")
    
    # Duck.ai settings
    DUCK_MODEL = "gpt-5.6-luna"
    DUCK_CHAT_URL = "https://duck.ai/duckchat/v1/chat"
    
    # Limits
    MAX_IMAGES = 4
    MAX_IMAGE_BYTES = 5 * 1024 * 1024
    MAX_BODY_BYTES = 16 * 1024 * 1024
    MAX_OUTPUT_IMAGES = 8
    
    # Token pool
    TOKEN_TTL = 1200
    TOKEN_POOL_TARGET = 15
