import os
import platform

# ============================================================
# ПУТИ — OCR
# ============================================================
if platform.system() == "Windows":
    TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    TESSDATA_PREFIX = r"C:\Program Files\Tesseract-OCR\tessdata"
    POPPLER_PATH = r"C:\Users\egorg\Documents\RAG_Egor\RAG_PRAVO\poppler\poppler-24.02.0\Library\bin"
else:
    TESSERACT_CMD = "tesseract"
    TESSDATA_PREFIX = "/usr/share/tesseract-ocr/5/tessdata"
    POPPLER_PATH = None

# ============================================================
# ПУТИ — ФАЙЛЫ И ПАПКИ
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAISS_INDEX_PATH = os.path.join(BASE_DIR, "faiss_laws_index")
DATASETS_DIR = os.path.join(BASE_DIR, "datasets")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
CHUNKS_CACHE_PATH = os.path.join(CACHE_DIR, "chunks.pkl")
TEXTS_CACHE_DIR = os.path.join(CACHE_DIR, "texts")

# ============================================================
# API — PRAVO.GOV.RU
# ============================================================
PRAVO_BASE_URL = "http://publication.pravo.gov.ru"

# ============================================================
# API — GIGACHAT
# ============================================================
AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "your-gigachat-auth-key")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2")

# ============================================================
# МОДЕЛЬ ЭМБЕДДИНГОВ
# ============================================================
EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
EMBEDDING_DEVICE = "cuda"
EMBEDDING_DIM = 1024

# ============================================================
# FAISS HNSW
# ============================================================
HNSW_M = 32
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_SEARCH = 100

# ============================================================
# РЕТРИВЕР
# ============================================================
RETRIEVER_K = 5
OCR_DPI = 300