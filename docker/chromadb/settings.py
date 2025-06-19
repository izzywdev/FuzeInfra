# ChromaDB Settings Configuration
# This file contains settings for the FuzeInfra ChromaDB instance

import os
import chromadb
from chromadb.config import Settings

# Database configuration
CHROMA_HOST = os.getenv("CHROMA_HOST", "0.0.0.0")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
PERSIST_DIRECTORY = os.getenv("PERSIST_DIRECTORY", "/chroma/chroma")

# ChromaDB Settings
chroma_settings = Settings(
    # Database persistence
    persist_directory=PERSIST_DIRECTORY,
    is_persistent=True,
    
    # Security settings
    allow_reset=True,  # Allow database reset (disable in production)
    
    # Performance settings
    chroma_db_impl="duckdb+parquet",
    chroma_api_impl="chromadb.api.fastapi.FastAPI",
    
    # Authentication (disabled for local development)
    chroma_client_auth_provider=None,
    chroma_client_auth_credentials=None,
    
    # Telemetry (disable for privacy)
    anonymized_telemetry=False,
    
    # Logging
    chroma_log_level="INFO",
)

# Collection configurations
DEFAULT_COLLECTION_METADATA = {
    "hnsw:space": "cosine",  # Default similarity metric
    "hnsw:construction_ef": 200,
    "hnsw:M": 16,
}

# Recommended embedding models
RECOMMENDED_EMBEDDING_MODELS = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "sentence-transformers/all-mpnet-base-v2", 
    "openai/text-embedding-ada-002",
    "huggingface/all-MiniLM-L6-v2"
]

# Usage examples and documentation
USAGE_EXAMPLES = """
# Example usage of FuzeInfra ChromaDB:

import chromadb
from chromadb.config import Settings

# Connect to the FuzeInfra ChromaDB instance
client = chromadb.HttpClient(host='localhost', port=8003)

# Create or get a collection
collection = client.get_or_create_collection(
    name="my_documents",
    metadata={"hnsw:space": "cosine"}
)

# Add documents
collection.add(
    documents=["This is document 1", "This is document 2"],
    metadatas=[{"source": "web"}, {"source": "book"}],
    ids=["doc1", "doc2"]
)

# Query documents
results = collection.query(
    query_texts=["search query"],
    n_results=5
)
""" 