import os
import sys

# Allow running as a script: `python src/ingest_water.py`
if __package__ is None and __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
from pypdf import PdfReader

from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter

# ----------------------------
# Load environment
# ----------------------------
load_dotenv()

WATER_DOC_PATH = os.getenv("WATER_DOC_PATH")
WATER_CHROMA_DIR = os.getenv("WATER_CHROMA_DIR")
WATER_CHROMA_COLLECTION = os.getenv("WATER_CHROMA_COLLECTION")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")

if not WATER_DOC_PATH or not WATER_CHROMA_DIR or not WATER_CHROMA_COLLECTION:
    raise ValueError("Missing required env vars for water ingestion")

# ----------------------------
# Load PDF
# ----------------------------
def load_pdf(path: str) -> str:
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(text)
    return "\n".join(pages)

print(f"📄 Loading document: {WATER_DOC_PATH}")
raw_text = load_pdf(WATER_DOC_PATH)

# ----------------------------
# Split into chunks
# ----------------------------
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=120
)

chunks = text_splitter.split_text(raw_text)
print(f"✂️  Split into {len(chunks)} chunks")

# ----------------------------
# Embeddings
# ----------------------------
embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)

# ----------------------------
# Chroma (auto-persistent)
# ----------------------------
print(f"🧠 Writing to Chroma at: {WATER_CHROMA_DIR}")

vectordb = Chroma(
    collection_name=WATER_CHROMA_COLLECTION,
    persist_directory=WATER_CHROMA_DIR,
    embedding_function=embeddings,
)

# OPTIONAL: clear existing documents safely
existing = vectordb._collection.count()
if existing > 0:
    print(f"⚠️  Clearing existing {existing} documents")
    vectordb._collection.delete(where={})

source_name = os.path.basename(WATER_DOC_PATH)
vectordb.add_texts(
    texts=chunks,
    metadatas=[{"source": source_name}] * len(chunks)
)

print("✅ Water ingestion complete (auto-persisted)")
