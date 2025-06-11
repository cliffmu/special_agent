import numpy as np
from langchain.embeddings import OpenAIEmbeddings
from langchain.docstore.document import Document
from .logger_helper import log_to_file
from .vector_index import build_vector_index, query_vector_index

def refine_entities_for_command(
    user_text,
    all_states,
    openai_api_key=None,
    k_vector=30
):
    """
    High-level function that:
      1) Filters out irrelevant sub-entities if needed
      2) Builds (or loads) the vector index
      3) Queries the index for top matches
      4) Re-ranks them based on domain / location
      5) Optionally refines sub-entities if advanced features are requested
      6) Returns final docs + sub-entities
    
    agent_logic.py can just call this once, and we handle all steps here.
    """

    # 1) Filter out obviously irrelevant or numeric LED param controls
    primary_states = filter_irrelevant_entities(all_states)

    # 2) Build or load vector index
    index_data = build_vector_index(primary_states, openai_api_key=openai_api_key)
    if index_data is None:
        log_to_file("[EntityRefinement] Error: vector index is None. Returning empty.")
        return [], []

    # 3) Query the vector index for top k
    top_docs = query_vector_index(index_data, user_text, k=k_vector, openai_api_key=openai_api_key)
    log_to_file(f"[EntityRefinement] Got {len(top_docs)} docs from vector index for '{user_text}'")

    # 4) Re-rank to highlight relevant domains/locations
    final_docs = rerank_and_filter_docs(user_text, top_docs)
    log_to_file(f"[EntityRefinement] Re-ranked => {len(final_docs)} final docs for context.")

    # 5) Optionally refine sub-entities if advanced controls are requested
    sub_entities = []
    if final_docs:
        best_doc = final_docs[0]
        parent_eid = best_doc.metadata.get("entity_id")
        sub_entities = refine_sub_entities(user_text, parent_eid, all_states, openai_api_key=openai_api_key)
        log_to_file(f"[EntityRefinement] Found {len(sub_entities)} advanced sub-entities for doc={parent_eid}")

    return final_docs, sub_entities


# def filter_irrelevant_entities(all_states):
#     """
#     Example filter removing numeric LED controls, etc.
#     Adjust logic as needed.
#     """
#     def is_irrelevant_sub_entity(s):
#         if s["domain"] == "number" and "led" in s["name"].lower():
#             return True
#         return False
    
#     filtered = [st for st in all_states if not is_irrelevant_sub_entity(st)]

#     log_to_file(
#     f"[EntityRefinement] Filtered out {len(all_states) - len(filtered)} entities. "
#     f"Indexing {len(filtered)} in main vector index."
#     )
#     return filtered

def filter_irrelevant_entities(all_states):
    """
    Filters out entities whose domain is in the excluded list, or
    numeric LED controls ('number' domain + 'led' in name).

    Adjust logic or domains as needed.
    """

    EXCLUDED_DOMAINS = {
        "number",
        "switch",
        "binary_sensor",
        "automation",
        "assist_satellite",
        "button",
        "camera",
        "conversation",
        "event",
        "input_select",
        "script",
        "select",
        "sensor",
        "stt",
        "sun",
        "tts",
        "time",
        "update",
        "wake_word",
        "zone",
    }

    def is_irrelevant_entity(s):
        domain = s.get("domain", "")
        name = s.get("name", "").lower()

        # 1) Exclude if domain is in the big excluded list
        if domain in EXCLUDED_DOMAINS:
            return True

        # 2) Exclude if domain == "number" and name contains "led"
        if domain == "number" and "led" in name:
            return True

        return False

    filtered = [st for st in all_states if not is_irrelevant_entity(st)]

    log_to_file(
        f"[EntityRefinement] Filtered out {len(all_states) - len(filtered)} entities. "
        f"Indexing {len(filtered)} in main vector index."
    )
    return filtered

# def rerank_and_filter_docs(user_text, docs, filter_qty=10):
#     """
#     Re-rank vector-search docs to favor certain domains and location keywords.
#     """
#     log_to_file(f"[EntityRefinement] here1")
#     text_lower = user_text.lower()

#     # Example naive location detection
#     location_hint = None
#     for loc_word in ["office", "living room", "bedroom", "nursery", "kitchen"]:
#         if loc_word in text_lower:
#             location_hint = loc_word
#             break

#     preferred_domains = ["light", "climate", "fan", "media_player", "switch", "cover"]

#     log_to_file(f"[EntityRefinement] here2")

#     scored = []
#     for i, doc in enumerate(docs):
#         log_to_file(f"[EntityRefinement] here3")
#         # doc[0] => top similarity, so let's invert for base_score
#         base_score = (len(docs) - i)  
#         domain = extract_domain(doc)
#         domain_bonus = 0
#         if domain in preferred_domains:
#             idx = preferred_domains.index(domain)
#             domain_bonus = (len(preferred_domains) - idx) * 5

#         location_bonus = 0
#         doc_content_lower = doc.page_content.lower()
#         if location_hint and location_hint in doc_content_lower:
#             location_bonus = 10

#         # small penalty for sensors or automations
#         sensor_penalty = 0
#         if domain in ["sensor", "binary_sensor", "automation"]:
#             sensor_penalty = -5

#         final_score = base_score + domain_bonus + location_bonus + sensor_penalty
#         scored.append((final_score, doc))

#     log_to_file(f"[EntityRefinement] here4")

#     scored.sort(key=lambda x: x[0], reverse=True)
#     best_docs = [pair[1] for pair in scored]

#     best_docs_top = best_docs[:filter_qty] # limit to top 10 for final context
#     log_to_file(f"[EntityRefinement] Re-ranked docs => {len(best_docs_top)} final for GPT context.")
#     return best_docs_top

def rerank_and_filter_docs(user_text, docs, filter_qty=10):
    text_lower = user_text.lower()

    # Example naive location detection
    location_hint = None
    for loc_word in ["office", "living room", "bedroom", "nursery", "kitchen"]:
        if loc_word in text_lower:
            location_hint = loc_word
            break

    preferred_domains = ["light", "climate", "fan", "media_player", "switch", "cover"]

    scored = []
    for i, doc in enumerate(docs):
        # doc is now a dict => doc["page_content"], doc["metadata"]
        base_score = (len(docs) - i)  
        
        # Extract domain from doc["metadata"] or doc["page_content"]
        domain = extract_domain(doc)  # We'll fix extract_domain below

        domain_bonus = 0
        if domain in preferred_domains:
            idx = preferred_domains.index(domain)
            domain_bonus = (len(preferred_domains) - idx) * 5

        # Check if user’s location_hint is in doc’s text
        location_bonus = 0
        doc_content_lower = doc["page_content"].lower()  # dict key, not doc.page_content
        if location_hint and location_hint in doc_content_lower:
            location_bonus = 10

        sensor_penalty = 0
        if domain in ["sensor", "binary_sensor", "automation"]:
            sensor_penalty = -5

        final_score = base_score + domain_bonus + location_bonus + sensor_penalty
        scored.append((final_score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_docs = [pair[1] for pair in scored]
    best_docs_top = best_docs[:filter_qty]
    log_to_file(f"[EntityRefinement] Re-ranked docs => {len(best_docs_top)} final for GPT context.")
    return best_docs_top


# def extract_domain(doc):
#     if "domain" in doc.metadata:
#         return doc.metadata["domain"]
#     ent_id = doc.metadata.get("entity_id", "")
#     if "." in ent_id:
#         return ent_id.split(".")[0]
#     return "unknown"

def extract_domain(doc: dict) -> str:
    """
    doc is a dict with: {
      "page_content": "...",
      "metadata": {"entity_id": "...", maybe other fields}
    }
    """
    meta = doc.get("metadata", {})
    # If you want to store domain in metadata as well:
    if "domain" in meta:
        return meta["domain"]

    ent_id = meta.get("entity_id", "")
    if "." in ent_id:
        return ent_id.split(".")[0]
    return "unknown"



def refine_sub_entities(user_text, parent_entity_id, all_states, openai_api_key=None, k=5):
    """
    Check if advanced controls are requested (LED, bass, etc.), then search sub-entities 
    that share the same device_id as parent_entity_id.
    """
    if not is_advanced_query(user_text):
        return []

    parent_device_id = None
    for s in all_states:
        if s["entity_id"] == parent_entity_id:
            parent_device_id = s["attributes"].get("device_id")
            break

    if not parent_device_id:
        log_to_file(f"[EntityRefinement] No parent device_id found for {parent_entity_id}.")
        return []

    candidate_entities = []
    for s in all_states:
        dev_id = s["attributes"].get("device_id")
        if dev_id and dev_id == parent_device_id:
            candidate_entities.append(s)

    if len(candidate_entities) <= 1:
        return []

    # embed user_text
    embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key)
    try:
        query_vector = embeddings_model.embed_query(user_text)
    except Exception as e:
        log_to_file(f"[EntityRefinement] Error embedding user_text for sub-entity search: {e}")
        return []

    query_vec = np.array(query_vector, dtype=np.float32)
    norm_q = np.linalg.norm(query_vec)
    if norm_q != 0:
        query_vec = query_vec / norm_q

    # Build short docs for each candidate
    sub_embeddings = []
    sub_docs = []
    for ent in candidate_entities:
        doc_text = (
            f"Entity ID: {ent['entity_id']}\n"
            f"Name: {ent.get('name', '')}\n"
            f"Domain: {ent.get('domain', '')}\n"
            f"State: {ent.get('state', '')}\n"
            f"Attributes: {ent.get('attributes', {})}\n"
        )
        try:
            vec = embeddings_model.embed_query(doc_text)
            vec_norm = np.linalg.norm(vec)
            if vec_norm != 0:
                vec = vec / vec_norm
            sub_embeddings.append(vec)
            sub_docs.append(ent)
        except Exception:
            sub_embeddings.append(np.zeros((1536,), dtype=np.float32))
            sub_docs.append(ent)

    sub_embeddings = np.array(sub_embeddings, dtype=np.float32)
    similarities = np.dot(sub_embeddings, query_vec)

    top_k_idx = similarities.argsort()[-k:][::-1]
    best_subs = []
    for idx in top_k_idx:
        best_subs.append(sub_docs[idx])
        log_to_file(f"[EntityRefinement] sub-entity match: {sub_docs[idx]['entity_id']} (score={similarities[idx]:.4f})")

    return best_subs


def is_advanced_query(user_text: str) -> bool:
    text = user_text.lower()
    adv_keywords = ["led", "bass", "night mode", "surround", "color_temp", "hue", "saturation"]
    return any(kw in text for kw in adv_keywords)



# WORKING OK 2/14 11:59pm
# import numpy as np
# from langchain.embeddings import OpenAIEmbeddings
# from .logger_helper import log_to_file


# def filter_irrelevant_entities(all_states):
#     """
#     Filter out known "useless" sub-entities from the main index.
#     Example: numeric LED controls, unneeded sensor stats, etc.
#     Adjust logic as you see fit for your environment.
#     """
#     def is_irrelevant_sub_entity(s):
#         domain = s["entity_id"].split(".")[0]
#         name_lower = s["name"].lower()
#         # Example: skip number.* with "led" in name
#         if domain == "number" and "led" in name_lower:
#             return True
#         # Potentially skip other advanced param controls, etc.
#         return False
    
#     filtered = [st for st in all_states if not is_irrelevant_sub_entity(st)]
#     return filtered


# def rerank_and_filter_docs(user_text, docs):
#     """
#     Re-rank & filter the top vector search docs to highlight relevant domains & location.
#     E.g., for "make the office cozy," we prefer lights, climate, media_player in 'office.'
#     Return a smaller subset (top 5-10).
#     """
#     text_lower = user_text.lower()

#     # Attempt to parse a naive location hint from the text
#     # Expand with a dictionary or your own approach
#     location_hint = None
#     for loc_word in ["office", "living room", "bedroom", "nursery", "kitchen"]:
#         if loc_word in text_lower:
#             location_hint = loc_word
#             break

#     # Example domain preference for "cozy" scenario
#     # (In practice, you might parse user_text for the word "cozy" to decide these.)
#     preferred_domains = ["light", "climate", "fan", "media_player", "switch", "cover"]
    
#     scored = []
#     # We'll assume doc[0] is the most relevant from the vector index, doc[1] is second, etc.
#     # so doc with index i in the loop => higher similarity is i=0
#     # We'll invert that to keep a bigger base score for earlier docs
#     for i, doc in enumerate(docs):
#         base_score = (len(docs) - i)  # doc 0 gets base_score = len(docs), doc 1 => len(docs)-1, etc.

#         # Domain bonus
#         domain = extract_domain(doc)
#         domain_bonus = 0
#         if domain in preferred_domains:
#             # the earlier it appears in the list, the bigger the bonus
#             domain_idx = preferred_domains.index(domain)
#             domain_bonus = (len(preferred_domains) - domain_idx) * 5

#         # Location bonus
#         doc_content_lower = doc.page_content.lower()
#         location_bonus = 0
#         if location_hint and location_hint in doc_content_lower:
#             location_bonus = 10

#         # Maybe penalize sensors or automation
#         sensor_penalty = 0
#         if domain in ["sensor", "binary_sensor", "automation"]:
#             sensor_penalty = -5

#         final_score = base_score + domain_bonus + location_bonus + sensor_penalty
#         scored.append((final_score, doc))

#     # Sort descending by final_score
#     scored.sort(key=lambda x: x[0], reverse=True)
#     best_docs = [pair[1] for pair in scored]

#     log_to_file(f"[EntityRefinement] Best docs: {best_docs}")

#     # Return top 10 for GPT context
#     return best_docs[:10]


# def extract_domain(doc):
#     """
#     Attempt to find domain from doc's metadata or from the entity_id line in page_content.
#     """
#     if "entity_id" in doc.metadata:
#         ent_id = doc.metadata["entity_id"]
#         if "." in ent_id:
#             return ent_id.split(".")[0]
#     # fallback parse from doc.page_content if needed
#     return "unknown"


# def refine_sub_entities(user_text, parent_entity_id, all_states, openai_api_key=None, k=5):
#     """
#     Optional advanced search among sub-entities that share a parent or device.
#     For example, we might look for numeric controls or special attributes if user specifically
#     requests advanced features like LED brightness, bass, night mode, etc.
    
#     Approach:
#     1. Identify sub-entities that are presumably linked by device_id or a naming pattern.
#     2. Build short docs for each sub-entity, embed them, compare with user_text.
#     3. Return top-K best matches.
#     """
#     # If no advanced features are implied, skip
#     if not is_advanced_query(user_text):
#         return []
    
#     parent_device_id = None
#     # Attempt to find the parent's device_id from all_states
#     for s in all_states:
#         if s["entity_id"] == parent_entity_id:
#             parent_device_id = s["attributes"].get("device_id")
#             break
    
#     if not parent_device_id:
#         log_to_file(f"[EntityRefinement] No parent device_id found for {parent_entity_id}.")
#         return []
    
#     # Gather potential sub-entities that share device_id
#     candidate_entities = []
#     for s in all_states:
#         dev_id = s["attributes"].get("device_id")
#         if dev_id and dev_id == parent_device_id:
#             candidate_entities.append(s)
    
#     if len(candidate_entities) <= 1:
#         # Means there's basically no other "sub-entity" aside from parent
#         return []
    
#     # Embed user_text
#     embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key)
#     try:
#         query_vector = embeddings_model.embed_query(user_text)
#         query_vec = np.array(query_vector, dtype=np.float32)
#         norm_q = np.linalg.norm(query_vec)
#         if norm_q != 0:
#             query_vec = query_vec / norm_q
#     except Exception as e:
#         log_to_file(f"[EntityRefinement] Error embedding user_text for sub-entity search: {e}")
#         return []
    
#     # Build mini-docs, embed
#     sub_embeddings = []
#     sub_docs = []
    
#     for ent in candidate_entities:
#         doc_text = (
#             f"Entity ID: {ent['entity_id']}\n"
#             f"Name: {ent.get('name', '')}\n"
#             f"State: {ent.get('state', '')}\n"
#             f"Attributes: {ent.get('attributes', {})}\n"
#         )
#         try:
#             vec = embeddings_model.embed_query(doc_text)
#             vec_norm = np.linalg.norm(vec)
#             if vec_norm != 0:
#                 vec = vec / vec_norm
#             sub_embeddings.append(vec)
#             sub_docs.append(ent)
#         except Exception:
#             sub_embeddings.append(np.zeros(1536, dtype=np.float32))
#             sub_docs.append(ent)
    
#     sub_embeddings = np.array(sub_embeddings, dtype=np.float32)
#     similarities = np.dot(sub_embeddings, query_vec)
    
#     top_k_idx = similarities.argsort()[-k:][::-1]
#     best_subs = []
#     for idx in top_k_idx:
#         best_subs.append(sub_docs[idx])
#         log_to_file(
#             f"[EntityRefinement] Found sub-entity match: {sub_docs[idx]['entity_id']} (score={similarities[idx]:.4f})"
#         )
    
#     return best_subs


# def is_advanced_query(user_text: str) -> bool:
#     """
#     Simple heuristic to detect if the user might be asking for advanced controls,
#     e.g., LED brightness, bass, night mode, etc.
#     Expand as needed.
#     """
#     text = user_text.lower()
#     keywords = ["led", "bass", "night mode", "surround", "color_temp", "hue", "saturation"]
#     return any(kw in text for kw in keywords)







# import numpy as np
# from langchain.embeddings import OpenAIEmbeddings
# from .logger_helper import log_to_file


# def refine_sub_entities(user_text, parent_entity_id, all_states, openai_api_key=None, k=5):
#     """
#     Optional advanced search among sub-entities that share a parent or device.
#     For example, we might look for numeric controls or special attributes if user specifically
#     requests advanced features like LED brightness, bass level, etc.
    
#     Approach:
#     1. Identify sub-entities that are presumably linked by device_id or a naming pattern.
#     2. Build short docs for each sub-entity, embed them, compare with user_text.
#     3. Return top-K best matches.
#     """
#     # If no advanced features are implied, skip
#     if not is_advanced_query(user_text):
#         return []
    
#     parent_device_id = None
#     # Attempt to find the parent's device_id from all_states
#     for s in all_states:
#         if s["entity_id"] == parent_entity_id:
#             parent_device_id = s["attributes"].get("device_id")
#             break
    
#     if not parent_device_id:
#         log_to_file(f"[EntityRefinement] No parent device_id found for {parent_entity_id}.")
#         return []
    
#     # Gather potential sub-entities that share device_id
#     candidate_entities = []
#     for s in all_states:
#         dev_id = s["attributes"].get("device_id")
#         if dev_id and dev_id == parent_device_id:
#             candidate_entities.append(s)
    
#     if len(candidate_entities) <= 1:
#         # Means there's basically no other "sub-entity" aside from parent
#         return []
    
#     # Embed user_text
#     embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key)
#     try:
#         query_vector = embeddings_model.embed_query(user_text)
#         query_vec = np.array(query_vector, dtype=np.float32)
#         norm_q = np.linalg.norm(query_vec)
#         if norm_q != 0:
#             query_vec = query_vec / norm_q
#     except Exception as e:
#         log_to_file(f"[EntityRefinement] Error embedding user_text for sub-entity search: {e}")
#         return []
    
#     # Build mini-docs, embed
#     sub_embeddings = []
#     sub_docs = []
    
#     for ent in candidate_entities:
#         doc_text = (
#             f"Entity ID: {ent['entity_id']}\n"
#             f"Name: {ent.get('name', '')}\n"
#             f"State: {ent.get('state', '')}\n"
#             f"Attributes: {ent.get('attributes', {})}\n"
#         )
#         try:
#             vec = embeddings_model.embed_query(doc_text)
#             vec_norm = np.linalg.norm(vec)
#             if vec_norm != 0:
#                 vec = vec / vec_norm
#             sub_embeddings.append(vec)
#             sub_docs.append(ent)
#         except Exception:
#             sub_embeddings.append(np.zeros(1536, dtype=np.float32))
#             sub_docs.append(ent)
    
#     sub_embeddings = np.array(sub_embeddings, dtype=np.float32)
#     similarities = np.dot(sub_embeddings, query_vec)
    
#     top_k_idx = similarities.argsort()[-k:][::-1]
#     best_subs = []
#     for idx in top_k_idx:
#         best_subs.append(sub_docs[idx])
#         log_to_file(f"[EntityRefinement] Found sub-entity match: {sub_docs[idx]['entity_id']} (score={similarities[idx]:.4f})")
    
#     return best_subs


# def is_advanced_query(user_text: str) -> bool:
#     """
#     Simple heuristic to detect if the user might be asking for advanced controls,
#     e.g., LED brightness, bass, night mode, etc.
#     Expand as needed.
#     """
#     text = user_text.lower()
#     keywords = ["led", "bass", "night mode", "surround", "color_temp", "hue", "saturation"]
#     return any(kw in text for kw in keywords)
