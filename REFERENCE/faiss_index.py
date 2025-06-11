import os
from langchain.embeddings import OpenAIEmbeddings
from langchain.docstore.document import Document
from langchain.vectorstores import FAISS
from .logger_helper import log_to_file

def build_faiss_index(ha_states, index_name="faiss_index"):
    """
    Build a FAISS index from Home Assistant device states using OpenAI embeddings.
    - ha_states: List of dicts representing HA device states.
    - index_name: The local directory name in which to save the FAISS index.
    
    Returns the FAISS index object (if successful) or None on error.
    """
    # Initialize the embeddings object (uses OpenAI API internally)
    embeddings = OpenAIEmbeddings()
    source_chunks = []

    # Create a document for each HA state
    for state in ha_states:
        attributes = state.get("attributes", {})
        attr_lines = "\n".join([f"{k}: {v}" for k, v in attributes.items()])
        page_content = (
            f"Entity: {state.get('entity_id')}\n"
            f"Name: {state.get('name')}\n"
            f"State: {state.get('state')}\n"
            f"Attributes:\n{attr_lines}"
        )
        metadata = {
            "entity_id": state.get("entity_id"),
            "name": state.get("name")
        }
        # Log the chunk for verification
        log_to_file(f"[FaissIndex] Creating chunk for {state.get('entity_id')}:\n{page_content}")
        source_chunks.append(Document(page_content=page_content, metadata=metadata))

    # Build the FAISS index from the documents
    try:
        db = FAISS.from_documents(source_chunks, embeddings)
        db.save_local(index_name)
        log_to_file(f"[FaissIndex] FAISS index built with {len(source_chunks)} documents and saved as '{index_name}'.")
        return db
    except Exception as e:
        log_to_file(f"[FaissIndex] Error building FAISS index: {e}")
        return None

def query_faiss(index, query_text, k=5):
    """
    Query the FAISS index for the top k similar device states.
    - index: The FAISS index object (created by build_faiss_index).
    - query_text: The user query string.
    - k: Number of results to return.
    
    Returns a list of Document objects representing the matching devices.
    """
    try:
        results = index.similarity_search(query_text, k=k)
        log_to_file(f"[FaissIndex] Query '{query_text}' returned {len(results)} results.")
        for i, doc in enumerate(results, start=1):
            # Log first 100 characters of the page_content for each result
            snippet = doc.page_content[:100].replace("\n", " ")
            log_to_file(f"[FaissIndex] Result {i}: Entity: {doc.metadata.get('entity_id')} - {snippet}...")
        return results
    except Exception as e:
        log_to_file(f"[FaissIndex] Error querying FAISS index: {e}")
        return None
