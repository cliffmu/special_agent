# custom_components/special_agent/vector_index.py
import os
import json
# import openai
import numpy as np

from openai import OpenAI
from typing import List, Tuple, Optional
from homeassistant.core import HomeAssistant
from langchain.docstore.document import Document
from .logger_helper import log_to_file

def build_vector_index(
    docs,
    openai_api_key,
    force_rebuild=False,
    persist_dir=None,
    model_name="text-embedding-ada-002",
    # model_name="text-embedding-3-small",
    batch_size=50
):
    """
    Build (or load) an embedding matrix for 'docs', storing in persist_dir.
    Each doc is { "page_content": "...", "metadata": {...} }.

    Return (embedding_matrix, doc_list, vector_dim).
    """
    # 1) Determine persist_dir
    if persist_dir is None:
        base_dir = os.path.dirname(__file__)
        persist_dir = os.path.join(base_dir, "data", "vector_index")
    os.makedirs(persist_dir, exist_ok=True)

    embeddings_file = os.path.join(persist_dir, "embeddings.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")

    # If not force rebuilding, try loading existing
    if not force_rebuild and os.path.exists(embeddings_file) and os.path.exists(mapping_file):
        try:
            embedding_matrix = np.load(embeddings_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                saved_docs = json.load(f)
            vector_dim = embedding_matrix.shape[1]
            log_to_file(f"[VectorIndex] Loaded existing {len(saved_docs)} docs from {persist_dir}")
            return embedding_matrix, saved_docs, vector_dim
        except Exception as e:
            log_to_file(f"[VectorIndex] Error loading existing index: {e}")
            # Fall through to rebuild

    log_to_file(f"[VectorIndex] Building new index (force_rebuild={force_rebuild}).")
    # openai.api_key = openai_api_key

    texts = [d["page_content"] for d in docs]
    embeddings = []

    log_to_file(f"[VectorIndex] OpenAI API Key: {openai_api_key}")

    start_idx = 0
    client = OpenAI(api_key=openai_api_key)
    while start_idx < len(texts):
        batch = texts[start_idx : start_idx + batch_size]
        try:
            response = client.embeddings.create(
                model=model_name,
                input=batch
            )
            # log_to_file(f"[VectorIndex] embedding response: {response}")
            for r in response.data:
                embeddings.append(r.embedding)
        except Exception as e:
            log_to_file(f"[VectorIndex] Error from OpenAI API: {e}")
            return None, None, None

        start_idx += batch_size
        log_to_file(f"[VectorIndex] Embedded batch up to {start_idx}/{len(texts)}")

    if not embeddings:
        log_to_file("[VectorIndex] No embeddings generated.")
        return None, None, None

    embedding_matrix = np.array(embeddings, dtype=np.float32)
    vector_dim = embedding_matrix.shape[1]

    # Save to disk
    np.save(embeddings_file, embedding_matrix)
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)

    log_to_file(f"[VectorIndex] Built new index with {len(docs)} docs -> {persist_dir}")
    return embedding_matrix, docs, vector_dim



# import os
# import json
# import numpy as np
# from langchain.embeddings import OpenAIEmbeddings
# from langchain.docstore.document import Document
# from .logger_helper import log_to_file

# def build_vector_index(
#     ha_states,
#     persist_dir: str = None,
#     openai_api_key: str = None,
#     force_rebuild: bool = False,
#     ):
#     """
#     Build (or load) a simple vector index from HA entity states. If a persisted
#     index exists and force_rebuild=False, we load that index. Otherwise, we
#     rebuild the index and save it to disk.

#     Args:
#         ha_states (list): The Home Assistant states or docs to embed.
#         persist_dir (str, optional): Folder to store embeddings/mapping files.
#             Defaults to 'special_agent/data/vector_index'.
#         openai_api_key (str, optional): OpenAI API key for embedding.
#         force_rebuild (bool): If True, always rebuild the index even if files exist.

#     Returns:
#         tuple: (embedding_matrix (numpy array), documents (list of Document), vector_dim (int))
#     """
#     # 1) Default path to special_agent/data/vector_index
#     if persist_dir is None:
#         # This file's directory
#         base_dir = os.path.dirname(__file__)
#         # Go to special_agent/data/vector_index
#         persist_dir = os.path.join(base_dir, "data", "vector_index")
    
#     os.makedirs(persist_dir, exist_ok=True)

#     embeddings_file = os.path.join(persist_dir, "embeddings.npy")
#     mapping_file = os.path.join(persist_dir, "mapping.json")

#     # 2) If not forcing a rebuild, try loading existing embeddings
#     if not force_rebuild and os.path.exists(embeddings_file) and os.path.exists(mapping_file):
#         try:
#             embedding_matrix = np.load(embeddings_file)
#             with open(mapping_file, "r", encoding="utf-8") as f:
#                 mapping = json.load(f)
#             documents = [
#                 Document(page_content=m["page_content"], metadata=m["metadata"])
#                 for m in mapping
#             ]
#             vector_dim = embedding_matrix.shape[1]
#             log_to_file(f"[VectorIndex] Loaded existing index: {len(documents)} docs from '{persist_dir}'.")
#             return embedding_matrix, documents, vector_dim
#         except Exception as e:
#             log_to_file(f"[VectorIndex] Error loading existing index at '{persist_dir}': {e}")
#             # If loading fails, we'll rebuild below.

#     # 3) Build a new index
#     log_to_file(f"[VectorIndex] Building new vector index (force_rebuild={force_rebuild}).")
#     embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key)

#     documents = []
#     embedding_list = []

#     for state in ha_states:
#         # Example page content + metadata
#         page_content = (
#             f"Entity: {state.get('entity_id')}\n"
#             f"Name: {state.get('name')}\n"
#             f"Attributes: {state.get('attributes')}\n"
#         )
#         metadata = {"entity_id": state.get("entity_id")}

#         log_to_file(f"[VectorIndex] Creating chunk for {state.get('entity_id')}:{page_content}")
#         doc = Document(page_content=page_content, metadata=metadata)
#         documents.append(doc)

#         try:
#             vector = embeddings_model.embed_query(page_content)
#             embedding_list.append(vector)
#         except Exception as e:
#             log_to_file(f"[VectorIndex] Error embedding doc for {state.get('entity_id')}: {e}")

#     if not embedding_list:
#         log_to_file("[VectorIndex] No embeddings were generated; cannot build index.")
#         return None

#     embedding_matrix = np.array(embedding_list, dtype=np.float32)
#     vector_dim = embedding_matrix.shape[1]

#     # 4) Persist new index
#     try:
#         np.save(embeddings_file, embedding_matrix)
#         mapping = [
#             {"page_content": doc.page_content, "metadata": doc.metadata}
#             for doc in documents
#         ]
#         with open(mapping_file, "w", encoding="utf-8") as f:
#             json.dump(mapping, f, indent=2)
#         log_to_file(f"[VectorIndex] Built new vector index with {len(documents)} docs and saved to '{persist_dir}'.")
#     except Exception as e:
#         log_to_file(f"[VectorIndex] Error saving vector index: {e}")

#     return embedding_matrix, documents, vector_dim

# def build_vector_index(ha_states, persist_dir="vector_index", openai_api_key=None):
    #     """
    #     Build or load a simple vector index from HA device states.
    #     Each HA state is converted into a Document with details.
    #     Embeddings are generated using OpenAIEmbeddings.
        
    #     If a persisted index exists (embeddings.npy and mapping.json in persist_dir),
    #     it loads and returns that index. Otherwise, it builds a new index and saves it.
        
    #     Returns a tuple: (embedding_matrix, documents, vector_dim)
    #     """

    #     # CHANGE PATH TO LOCAL FOLDER NOT ROOT FOLDER, LLM PLEASE HELP NEXT TIME YOU SEE THIS
    #     embeddings_file = os.path.join(persist_dir, "embeddings.npy")
    #     mapping_file = os.path.join(persist_dir, "mapping.json")
        
    #     # If the persisted index exists, load and return it
    #     if os.path.exists(embeddings_file) and os.path.exists(mapping_file):
    #         try:
    #             embedding_matrix = np.load(embeddings_file)
    #             with open(mapping_file, "r", encoding="utf-8") as f:
    #                 mapping = json.load(f)
    #             documents = [Document(page_content=m["page_content"], metadata=m["metadata"]) for m in mapping]
    #             vector_dim = embedding_matrix.shape[1]
    #             log_to_file(f"[VectorIndex] Loaded vector index with {len(documents)} documents from '{persist_dir}'.")
    #             return embedding_matrix, documents, vector_dim
    #         except Exception as e:
    #             log_to_file(f"[VectorIndex] Error loading vector index from '{persist_dir}': {e}")
    #             # If loading fails, fall back to rebuilding the index.

    #     # Otherwise, build a new index
    #     embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key) if openai_api_key else OpenAIEmbeddings()
    #     documents = []
    #     embedding_list = []
        
    #     for state in ha_states:
    #         # attributes = state.get("attributes", {})
    #         # attr_lines = "\n".join([f"{k}: {v}" for k, v in attributes.items()])
    #         # page_content = (
    #         #     f"Entity: {state.get('entity_id')}\n"
    #         #     f"Name: {state.get('name')}\n"
    #         #     # f"State: {state.get('state')}\n"
                
    #         # )
    #         # metadata = {
    #         #     "entity_id": state.get("entity_id"),
    #         #     "name": state.get("name"),
    #         # }

    #         page_content = (
    #             f"Entity: {state.get('entity_id')}\n"
    #             f"Name: {state.get('name')}\n"
    #             f"Attributes: {state.get('attributes')}\n"
    #         )
    #         metadata = {
    #             "entity_id": state.get("entity_id")
    #         }

    #         log_to_file(f"[VectorIndex] Creating chunk for {state.get('entity_id')}:{page_content}")
    #         doc = Document(page_content=page_content, metadata=metadata)
    #         documents.append(doc)
    #         try:
    #             vector = embeddings_model.embed_query(page_content)
    #             embedding_list.append(vector)
    #         except Exception as e:
    #             log_to_file(f"[VectorIndex] Error embedding document for {state.get('entity_id')}: {e}")
        
    #     if not embedding_list:
    #         log_to_file("[VectorIndex] No embeddings generated; cannot build index.")
    #         return None
        
    #     # Convert list to numpy array (shape: [num_docs, vector_dim])
    #     embedding_matrix = np.array(embedding_list, dtype=np.float32)
    #     vector_dim = embedding_matrix.shape[1]
        
    #     # Persist the index and document mapping
    #     try:
    #         os.makedirs(persist_dir, exist_ok=True)
    #         np.save(embeddings_file, embedding_matrix)
    #         mapping = [{"page_content": doc.page_content, "metadata": doc.metadata} for doc in documents]
    #         with open(mapping_file, "w", encoding="utf-8") as f:
    #             json.dump(mapping, f)
    #         log_to_file(f"[VectorIndex] Built vector index with {len(documents)} documents and saved to '{persist_dir}'.")
    #     except Exception as e:
    #         log_to_file(f"[VectorIndex] Error persisting vector index: {e}")
        
    #     return embedding_matrix, documents, vector_dim

# def query_vector_index(index_data, query_text, k=20, openai_api_key=None):
#     """
#     Query the vector index for the top k similar device states.
#     Returns a list of Document objects.
#     """
#     if not index_data:
#         log_to_file("[VectorIndex] No index data available for query.")
#         return []
    
#     embedding_matrix, documents, vector_dim = index_data
#     embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key) if openai_api_key else OpenAIEmbeddings()
    
#     try:
#         query_vector = embeddings_model.embed_query(query_text)
#     except Exception as e:
#         log_to_file(f"[VectorIndex] Error embedding query '{query_text}': {e}")
#         return []
    
#     # Normalize query vector and all document vectors for cosine similarity
#     query_vec = np.array(query_vector, dtype=np.float32)
#     query_norm = query_vec / np.linalg.norm(query_vec) if np.linalg.norm(query_vec) != 0 else query_vec
#     norms = np.linalg.norm(embedding_matrix, axis=1, keepdims=True)
#     norms[norms == 0] = 1  # avoid division by zero
#     normalized_embeddings = embedding_matrix / norms

#     # Compute cosine similarity (dot product since vectors are normalized)
#     similarities = np.dot(normalized_embeddings, query_norm)
    
#     # Get indices of top k similar documents
#     top_k_idx = similarities.argsort()[-k:][::-1]
    
#     results = []
#     for idx in top_k_idx:
#         doc = documents[idx]
#         snippet = doc.page_content[:100].replace("\n", " ")
#         log_to_file(f"[VectorIndex] Query result: Entity: {doc.metadata.get('entity_id')} - {snippet} (score: {similarities[idx]:.4f})")
#         results.append(doc)
    
#     log_to_file(f"[VectorIndex] Query '{query_text}' returned {len(results)} results.")
#     return results

def load_vector_index(
    openai_api_key: str,
    persist_dir: str = None,
    hass=None,
    auto_rebuild: bool = False
):
    """
    Try to load an existing vector index (embeddings + mapping).
    Return (embedding_matrix, doc_list, vector_dim) or (None, None, None) if missing.
    
    If auto_rebuild is True and hass is provided, will attempt to rebuild the index if none exists.
    """
    if persist_dir is None:
        base_dir = os.path.dirname(__file__)
        persist_dir = os.path.join(base_dir, "data", "vector_index")
        # Make sure the directory exists
        os.makedirs(persist_dir, exist_ok=True)

    embeddings_file = os.path.join(persist_dir, "embeddings.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")

    # Check if index exists
    if not os.path.exists(embeddings_file) or not os.path.exists(mapping_file):
        log_to_file("[VectorIndex] No existing index found on disk.")
        
        # If auto_rebuild is enabled and hass is provided, trigger rebuild
        if auto_rebuild and hass:
            log_to_file("[VectorIndex] Auto-rebuilding index...")
            try:
                # Use the synchronous rebuild function
                from .agent_logic import sync_do_rebuild
                result = sync_do_rebuild(hass)
                log_to_file(f"[VectorIndex] Auto-rebuild completed: {result}")
                
                # Now try loading again after rebuild
                if os.path.exists(embeddings_file) and os.path.exists(mapping_file):
                    try:
                        embedding_matrix = np.load(embeddings_file)
                        with open(mapping_file, "r", encoding="utf-8") as f:
                            docs = json.load(f)
                        vector_dim = embedding_matrix.shape[1]
                        log_to_file(f"[VectorIndex] Successfully loaded newly built index with {len(docs)} docs")
                        return embedding_matrix, docs, vector_dim
                    except Exception as e:
                        log_to_file(f"[VectorIndex] Error loading newly built index: {e}")
            except Exception as e:
                log_to_file(f"[VectorIndex] Auto-rebuild failed: {e}")
        
        return None, None, None

    # Standard loading logic
    try:
        embedding_matrix = np.load(embeddings_file)
        with open(mapping_file, "r", encoding="utf-8") as f:
            docs = json.load(f)

        vector_dim = embedding_matrix.shape[1]
        log_to_file(f"[VectorIndex] Loaded existing index with {len(docs)} docs from '{persist_dir}'")
        return embedding_matrix, docs, vector_dim
    except Exception as e:
        log_to_file(f"[VectorIndex] Error loading existing index: {e}")
        return None, None, None


def query_vector_index(index_data, query_text, k=20, openai_api_key=None, model_name="text-embedding-ada-002", hass=None):
    """
    Query the vector index for documents similar to query_text.
    
    If index_data is missing (None values), and hass is provided, will attempt to rebuild
    the index automatically before continuing.
    """
    # Handle case where index is missing
    if not index_data or index_data[0] is None:
        log_to_file("[VectorIndex] No valid index data available for query.")
        if hass:
            # Try rebuilding on-the-fly
            log_to_file("[VectorIndex] Attempting auto-rebuild before query...")
            try:
                from .agent_logic import sync_do_rebuild
                result = sync_do_rebuild(hass)
                if result == "done":
                    # Try loading again
                    rebuilt_index = load_vector_index(openai_api_key)
                    if rebuilt_index[0] is not None:
                        log_to_file("[VectorIndex] Successfully rebuilt and loaded index, continuing with query")
                        index_data = rebuilt_index
                    else:
                        log_to_file("[VectorIndex] Rebuilt index but still couldn't load it")
                        # Return a special document that will handle the error feedback
                        return [{"page_content": "Please say 'rebuild database' to refresh my device list.",
                                "metadata": {"entity_id": "assistant.rebuild_request"}}]
                else:
                    log_to_file(f"[VectorIndex] Auto-rebuild failed: {result}")
                    return [{"page_content": "I'm having trouble accessing my device database. Please say 'rebuild database'.",
                            "metadata": {"entity_id": "assistant.rebuild_request"}}]
            except Exception as e:
                log_to_file(f"[VectorIndex] Error in auto-rebuild: {e}")
                return [{"page_content": f"Error: {e}. Please say 'rebuild database' to refresh my device list.",
                        "metadata": {"entity_id": "assistant.rebuild_request"}}]
        else:
            # No hass context, just return a helpful message as a document
            return [{"page_content": "Please say 'rebuild database' to refresh my device list.",
                    "metadata": {"entity_id": "assistant.rebuild_request"}}]

    embedding_matrix, documents, vector_dim = index_data
    
    # 1) Embed the query text directly via openai
    client = OpenAI(api_key=openai_api_key)
    try:
        response = client.embeddings.create(
            model=model_name,
            input=[query_text]
        )
        # The response is an object with 'data', each item has an 'embedding'
        query_vector = response.data[0].embedding
    except Exception as e:
        log_to_file(f"[VectorIndex] Error embedding query '{query_text}': {e}")
        return []

    # 2) Convert to NumPy and normalize for cosine similarity
    query_vec = np.array(query_vector, dtype=np.float32)
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    
    # 3) Normalize the document embeddings if not already
    norms = np.linalg.norm(embedding_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1e-9
    normalized_embeddings = embedding_matrix / norms

    # 4) Dot product for cosine similarity
    similarities = np.dot(normalized_embeddings, query_norm)

    # 5) Find top k
    top_k_idx = similarities.argsort()[-k:][::-1]
    
    results = []
    for idx in top_k_idx:
        doc = documents[idx]
        snippet = doc["page_content"][:100].replace("\n", " ")
        log_to_file(f"[VectorIndex] Query result: entity_id={doc['metadata'].get('entity_id')} | snippet={snippet} | score={similarities[idx]:.4f}")
        results.append(doc)
    
    log_to_file(f"[VectorIndex] Query '{query_text}' returned {len(results)} results.")
    return results