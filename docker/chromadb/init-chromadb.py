#!/usr/bin/env python3
"""
ChromaDB Initialization Script for FuzeInfra
Creates default collections and example data
"""

import time
import requests
import chromadb
from chromadb.config import Settings
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def wait_for_chromadb(host="localhost", port=8003, max_retries=30):
    """Wait for ChromaDB to be ready"""
    for i in range(max_retries):
        try:
            response = requests.get(f"http://{host}:{port}/api/v1/heartbeat")
            if response.status_code == 200:
                logger.info("ChromaDB is ready!")
                return True
        except requests.exceptions.ConnectionError:
            logger.info(f"Waiting for ChromaDB... ({i+1}/{max_retries})")
            time.sleep(2)
    
    logger.error("ChromaDB failed to start within timeout period")
    return False

def create_example_collections():
    """Create example collections with sample data"""
    try:
        client = chromadb.HttpClient(host='localhost', port=8003)
        
        # Collection 1: Documents
        logger.info("Creating 'documents' collection...")
        documents_collection = client.get_or_create_collection(
            name="documents",
            metadata={
                "hnsw:space": "cosine",
                "description": "General document storage and retrieval"
            }
        )
        
        # Add sample documents
        documents_collection.add(
            documents=[
                "FuzeInfra is a comprehensive development platform providing managed services for modern applications.",
                "ChromaDB integration enables vector search and semantic similarity for AI-powered applications.",
                "The platform includes PostgreSQL, MongoDB, Redis, Kafka, and monitoring tools out of the box.",
                "Developers can quickly deploy applications without worrying about infrastructure setup.",
                "Vector databases are essential for building RAG (Retrieval-Augmented Generation) applications."
            ],
            metadatas=[
                {"type": "platform", "category": "overview"},
                {"type": "technical", "category": "ai"},
                {"type": "technical", "category": "databases"},
                {"type": "developer", "category": "workflow"},
                {"type": "technical", "category": "ai"}
            ],
            ids=["doc1", "doc2", "doc3", "doc4", "doc5"]
        )
        logger.info("✓ Documents collection created with sample data")
        
        # Collection 2: Knowledge Base
        logger.info("Creating 'knowledge_base' collection...")
        kb_collection = client.get_or_create_collection(
            name="knowledge_base",
            metadata={
                "hnsw:space": "cosine",
                "description": "Company knowledge base and documentation"
            }
        )
        
        kb_collection.add(
            documents=[
                "How to deploy a new project using FuzeInfra platform",
                "Troubleshooting common Docker container issues",
                "Best practices for database connection management",
                "Setting up monitoring and alerting for applications",
                "Configuring environment variables for different environments"
            ],
            metadatas=[
                {"type": "tutorial", "difficulty": "beginner"},
                {"type": "troubleshooting", "difficulty": "intermediate"},
                {"type": "best_practice", "difficulty": "intermediate"},
                {"type": "tutorial", "difficulty": "advanced"},
                {"type": "configuration", "difficulty": "beginner"}
            ],
            ids=["kb1", "kb2", "kb3", "kb4", "kb5"]
        )
        logger.info("✓ Knowledge base collection created with sample data")
        
        # Collection 3: Code Examples
        logger.info("Creating 'code_examples' collection...")
        code_collection = client.get_or_create_collection(
            name="code_examples",
            metadata={
                "hnsw:space": "cosine",
                "description": "Code snippets and examples"
            }
        )
        
        code_collection.add(
            documents=[
                "Python function to connect to PostgreSQL database using psycopg2",
                "JavaScript code for sending HTTP requests to REST APIs",
                "Docker compose configuration for web application deployment",
                "SQL query examples for data aggregation and reporting",
                "Python script for processing CSV files and data transformation"
            ],
            metadatas=[
                {"language": "python", "type": "database"},
                {"language": "javascript", "type": "api"},
                {"language": "yaml", "type": "infrastructure"},
                {"language": "sql", "type": "query"},
                {"language": "python", "type": "data_processing"}
            ],
            ids=["code1", "code2", "code3", "code4", "code5"]
        )
        logger.info("✓ Code examples collection created with sample data")
        
        logger.info("🎉 All example collections created successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Error creating collections: {e}")
        return False

def display_usage_examples():
    """Display usage examples for users"""
    examples = """
    
    📚 CHROMADB USAGE EXAMPLES:
    
    # Python client example:
    import chromadb
    client = chromadb.HttpClient(host='localhost', port=8003)
    
    # Query documents
    collection = client.get_collection("documents")
    results = collection.query(
        query_texts=["platform development"],
        n_results=3
    )
    
    # REST API example:
    curl -X POST http://localhost:8003/api/v1/collections/documents/query \\
      -H "Content-Type: application/json" \\
      -d '{"query_texts": ["development platform"], "n_results": 3}'
    
    # Available collections:
    - documents: General document storage
    - knowledge_base: Company knowledge and documentation  
    - code_examples: Code snippets and examples
    
    """
    logger.info(examples)

def main():
    """Main initialization function"""
    logger.info("🚀 Starting ChromaDB initialization for FuzeInfra...")
    
    # Wait for ChromaDB to be ready
    if not wait_for_chromadb():
        logger.error("❌ ChromaDB initialization failed - service not ready")
        return False
    
    # Create example collections
    if create_example_collections():
        display_usage_examples()
        logger.info("✅ ChromaDB initialization completed successfully!")
        return True
    else:
        logger.error("❌ ChromaDB initialization failed during collection setup")
        return False

if __name__ == "__main__":
    main() 