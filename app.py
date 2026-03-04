import os
import json
from io import BytesIO

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from pageindex import page_index_main
from pageindex.utils import (
    ConfigLoader, 
    remove_fields, 
    ChatGPT_API_async,
    extract_json,
    JsonLogger
)
from dotenv import load_dotenv

def create_node_mapping(tree, mapping=None):
    if mapping is None:
        mapping = {}
    if isinstance(tree, list):
        for node in tree:
            create_node_mapping(node, mapping)
    elif isinstance(tree, dict):
        if "node_id" in tree:
            mapping[tree["node_id"]] = tree
        if "nodes" in tree:
            create_node_mapping(tree["nodes"], mapping)
    return mapping

# Load environment variables from .env file
load_dotenv()

app = FastAPI(
    title="PageIndex RAG API",
    description="Upload a PDF and chat with it using PageIndex Vectorless RAG.",
    version="1.0.0",
)

# Load default config from config.yaml once at startup
config_loader = ConfigLoader()
default_opt = config_loader.load({})
# OVERRIDE: We MUST add text to the node, otherwise we cannot perform RAG chatting
default_opt.if_add_node_text = "yes"

RESULTS_DIR = "./results"
os.makedirs(RESULTS_DIR, exist_ok=True)


class ChatRequest(BaseModel):
    query: str
    pdf_name: str


@app.post("/upload-pdf/", summary="Upload a PDF and generate PageIndex tree")
def upload_pdf(file: UploadFile = File(..., description="A PDF file to process")):
    """
    Upload a PDF document. The server will:
    1. Parse the PDF using the PageIndex library.
    2. Generate a hierarchical tree-structure JSON.
    3. Save the JSON to the `./results/` directory.
    4. Return the JSON in the response.
    """
    # Validate file type
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    try:
        # Read the uploaded file into memory
        pdf_bytes = BytesIO(file.file.read())

        # Process through PageIndex (uses asyncio.run internally, so this must be sync)
        result = page_index_main(pdf_bytes, default_opt)

        # Save the tree JSON to disk
        pdf_name = os.path.splitext(file.filename)[0]
        output_file = os.path.join(RESULTS_DIR, f"{pdf_name}_structure.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        return {
            "message": f"Tree structure saved to {output_file}",
            "tree": result,
            "chat_ready_name": pdf_name
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/", summary="Chat with a processed PDF")
async def chat_with_pdf(request: ChatRequest):
    """
    Perform Vectorless, Reasoning-Based RAG over a previously uploaded PDF.
    Requires the `pdf_name` (filename without '.pdf') and the user's `query`.
    """
    
    # Initialize the Logger for this specific chat request
    # To keep names distinct, we add a "chat_" prefix
    logger = JsonLogger(f"chat_{request.pdf_name}.pdf")
    logger.info("Starting Chat Request", query=request.query, target_pdf=request.pdf_name)

    tree_file = os.path.join(RESULTS_DIR, f"{request.pdf_name}_structure.json")
    if not os.path.isfile(tree_file):
        logger.error("No tree structure found", tree_file=tree_file)
        raise HTTPException(
            status_code=404, 
            detail=f"No tree structure found for '{request.pdf_name}'. Please upload the PDF first."
        )

    try:
        # Load the tree JSON
        with open(tree_file, "r", encoding="utf-8") as f:
            tree_response = json.load(f)
            
        # The tree usually has a 'structure' key from page_index_main, or it might just be the array.
        tree = tree_response.get("structure") if "structure" in tree_response else tree_response

        # --- STEP 1: Reasoning-Based Retrieval ---
        logger.info("Step 1: Reasoning-Based Retrieval (Tree Search)")
        # Strip the full text from the tree so it fits nicely in the LLM context prompt
        tree_without_text = remove_fields(tree.copy(), fields=['text'])

        search_prompt = f"""
You are given a question and a tree structure of a document.
Each node contains a node id, node title, and a corresponding summary.
Your task is to find all nodes that are likely to contain the answer to the question.

Question: {request.query}

Document tree structure:
{json.dumps(tree_without_text, indent=2, ensure_ascii=False)}

Please reply in the following JSON format:
{{
    "thinking": "<Your thinking process on which nodes are relevant to the question>",
    "node_list": ["node_id_1", "node_id_2", ..., "node_id_n"]
}}
Directly return the final JSON structure. Do not output anything else.
"""
        logger.debug("Prompt sent for Tree Search", prompt_preview=search_prompt[:500] + "...\n[TRUNCATED]")

        # Call LLM to find the best nodes
        tree_search_result_str = await ChatGPT_API_async(
            model=default_opt.model, 
            prompt=search_prompt
        )
        
        # Parse the JSON response
        tree_search_result = extract_json(tree_search_result_str)
        node_ids_to_fetch = tree_search_result.get("node_list", [])
        thinking_process = tree_search_result.get("thinking", "")
        
        logger.info("Tree Search Result", 
                    thinking=thinking_process, 
                    retrieved_node_ids=node_ids_to_fetch)

        # --- STEP 2: Answer Generation ---
        logger.info("Step 2: Answer Generation (Context Fetching)")
        # Fetch the FULL TEXT for the nodes chosen by the LLM
        node_map = create_node_mapping(tree)
        relevant_content = ""
        retrieved_nodes_info = []

        for node_id in node_ids_to_fetch:
            if node_id in node_map:
                node = node_map[node_id]
                extracted_text = "\n\n".join(node.get("text", []))
                relevant_content += f"\n\n--- Node: {node.get('title')} ---\n{extracted_text}"
                
                retrieved_nodes_info.append({
                    "node_id": node_id,
                    "title": node.get("title"),
                    "page": node.get("page_index", "unknown")
                })
        
        logger.info("Nodes Fetched Successfully", fetched_nodes=retrieved_nodes_info)

        if not relevant_content.strip():
            logger.info("No relevant context found in fetched nodes")
            return {
                "answer": "I could not find relevant context in the document to answer your question.",
                "thinking": thinking_process,
                "retrieved_nodes": []
            }

        answer_prompt = f"""
Answer the question based on the context provided below.

Question: {request.query}
Context: {relevant_content}

Provide a clear, concise answer based only on the context provided.
"""
        logger.debug("Prompt sent for Answer Generation", prompt_preview=answer_prompt[:500] + "...\n[TRUNCATED]")
        
        # Call LLM to generate the final answer based on the fetched text
        final_answer = await ChatGPT_API_async(
            model=default_opt.model, 
            prompt=answer_prompt
        )
        
        logger.info("Final Answer Generated", answer=final_answer)

        return {
            "answer": final_answer,
            "thinking": thinking_process,
            "retrieved_nodes": retrieved_nodes_info
        }

    except Exception as e:
        logger.exception("Error processing chat endpoint", error_details=str(e))
        raise HTTPException(status_code=500, detail=f"Error processing chat: {str(e)}")
