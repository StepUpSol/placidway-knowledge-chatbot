"""Central configuration. Everything tunable lives here so it is easy to change live."""
import os

# The only pages the bot is allowed to know about. Nothing else is crawled.
SOURCE_URLS = [
    {"url": "https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
     "type": "Clinic profile"},
    {"url": "https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
     "type": "Treatment package"},
    {"url": "https://www.placidway.com/package/7902/Alternative-Adrenal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
     "type": "Treatment package"},
    {"url": "https://www.placidway.com/package/7901/Alternative-Anal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
     "type": "Treatment package"},
    {"url": "https://www.placidway.com/profile-videos/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
     "type": "Clinic videos"},
    {"url": "https://www.placidway.com/price-list/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
     "type": "Price list"},
    {"url": "https://www.placidway.com/search-medical-pricings/Alternative-Medicine/All/1",
     "type": "Price comparison"},
]

# Where patients are sent when the bot cannot help. Both links appear on the source pages.
QUOTE_URL = "https://www.placidway.com/rfi/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center"
CONTACT_URL = "https://www.placidway.com/contact-us"

# Crawler etiquette
USER_AGENT = "PlacidWayAssessmentBot/1.0 (website knowledge chatbot; contact: umarshafqat502@gmail.com)"
REQUEST_DELAY_SECONDS = 1.2  # at most one request per second

# Models
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMENSIONS = 512

# Chunking and retrieval
CHUNK_MAX_CHARS = 1100
TOP_K = 10
MIN_SIMILARITY = 0.25  # below this a chunk is treated as unrelated

# Paths
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")  # kb-part*.json.gz: chunks only; embeddings are rebuilt and cached on first use
KB_PART_SIZE = 100  # chunks per file, keeps each file small and diffs readable
RUNTIME_KB_PATH = "/tmp/kb.json"  # written by /api/refresh on the server

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # protects /api/refresh and /api/gaps
