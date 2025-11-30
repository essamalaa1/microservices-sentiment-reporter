from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import redis
import os
import requests
import pandas as pd
import io
import re
import markdown2
from langchain_ollama import OllamaLLM

app = FastAPI()

# --- Configuration ---
# Services are accessed by their docker-compose service names
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-db:6379")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
# Note: Ensure you use a PDF service image that accepts HTML POST requests, 
# or swap this logic back to WeasyPrint if you install dependencies in Dockerfile.
PDF_SERVICE_URL = os.getenv("PDF_SERVICE_URL", "http://pdf-service")

# --- Redis Connection ---
try:
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    print(f"Warning: Redis connection failed: {e}")

# --- Pydantic Models for Requests ---
class PreviewRequest(BaseModel):
    sheet_id: str

class BatchProcessRequest(BaseModel):
    sheet_id: str
    batch_size: int
    selected_cols: list[str]
    model_label: str  # e.g., "LLaMA 3 (8b)"
    reset_index: bool = False

class PdfRequest(BaseModel):
    markdown_content: str

# --- Helper Functions ---
SYSTEM_PROMPT_TEMPLATE = """You are an expert data analyst for cafes and restaurants...
(Your full system prompt here - omitted for brevity, keeping same logic)
...
### Report for Batch {batch_range}
...
"""

MODEL_MAP = {
    "DeepSeek R1 (1.5B)": "deepseek-r1:1.5b",
    "DeepSeek R1 (8B)": "deepseek-r1:8b",
    "LLaMA 3.2 (1B)": "llama3.2:1b",
    "LLaMA 3 (8b)": "llama3:latest",
    "LLaMA 3.2 (3.2b)": "llama3.2:latest"
}

def _fetch_sheet_data(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = df.columns.str.strip()
    
    # --- THE FIX: Replace NaN (empty cells) with empty strings ---
    df = df.fillna("") 
    
    return df

# --- API Endpoints ---

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Sentiment Backend"}

@app.post("/preview")
def get_preview(request: PreviewRequest):
    """Fetches the first 5 rows of the sheet for the UI."""
    try:
        df = _fetch_sheet_data(request.sheet_id)
        # Convert to JSON compatible format (records)
        return {
            "columns": df.columns.tolist(),
            "preview_data": df.head().to_dict(orient="records"),
            "total_rows": len(df)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/process-batch")
def process_batch(req: BatchProcessRequest):
    """
    Core Logic:
    1. Check Redis for last_row index.
    2. Fetch Sheet.
    3. If new rows exist > batch_size, process with LLM.
    4. Update Redis.
    """
    redis_key = f"state:{req.sheet_id}:last_row"
    
    # Handle Reset
    if req.reset_index:
        r.set(redis_key, 0)
        start_index = 0
    else:
        start_index = int(r.get(redis_key) or 0)

    try:
        df = _fetch_sheet_data(req.sheet_id)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to fetch sheet: {str(e)}"})

    total_rows = len(df)
    
    # Safety check: if sheet shrank or was replaced
    if start_index > total_rows:
        start_index = 0
        r.set(redis_key, 0)

    num_new_rows = total_rows - start_index
    
    if num_new_rows >= req.batch_size:
        # --- PROCESS BATCH ---
        batch_df = df.iloc[start_index : start_index + req.batch_size]
        batch_range_str = f"{start_index + 1}-{start_index + len(batch_df)}"
        
        # Format Text
        lines = []
        for _, row in batch_df.iterrows():
            parts = [str(row[c]).strip() for c in req.selected_cols if c in row.index and pd.notnull(row[c])]
            if parts:
                lines.append(" | ".join(parts))
        reviews_text = "\n".join(lines)

        # Call LLM
        model_name = MODEL_MAP.get(req.model_label, "llama3:latest")
        llm = OllamaLLM(model=model_name, base_url=OLLAMA_URL, temperature=0.3)
        
        prompt = SYSTEM_PROMPT_TEMPLATE.format(batch_range=batch_range_str)
        try:
            # We construct the message manually for OllamaLLM
            report = llm.invoke(f"System: {prompt}\n\nUser: Here are the reviews:\n{reviews_text}")
        except Exception as e:
             return {"status": "error", "message": f"LLM Failure: {str(e)}"}

        # Update State
        new_index = start_index + len(batch_df)
        r.set(redis_key, new_index)

        return {
            "status": "processed",
            "report_markdown": report,
            "new_last_row": new_index,
            "batch_range": batch_range_str
        }

    else:
        # --- WAITING ---
        return {
            "status": "waiting",
            "message": f"Only {num_new_rows} new rows. Need {req.batch_size}.",
            "rows_pending": num_new_rows,
            "last_row_processed": start_index
        }

@app.post("/generate-pdf")
def generate_pdf(req: PdfRequest):
    """
    Converts Markdown -> HTML -> PDF.
    Uses an external PDF microservice (wkhtmltopdf) to avoid installing heavy libs here.
    """
    try:
        # 1. Convert Markdown to HTML
        html_content = markdown2.markdown(req.markdown_content, extras=["tables"])
        
        # 2. Wrap in basic styling
        full_html = f"""
        <html>
        <head><style>body {{ font-family: sans-serif; }}</style></head>
        <body>{html_content}</body>
        </html>
        """

        # 3. Send to PDF Microservice
        # (Assuming openlabs/docker-wkhtmltopdf-aas API structure)
        # If using a different image, this payload format might change.
        payload = {
            "contents": full_html.encode("utf-8").decode("latin1"), # Handling encoding for specific API
            # OR simple raw html depending on the service you chose
        }
        
        # If using openlabs/docker-wkhtmltopdf-aas, it often expects a file upload or specific JSON
        # For simplicity, if we assume a service that takes HTML string:
        resp = requests.post(f"{PDF_SERVICE_URL}/", json={"contents": full_html}, timeout=10)
        
        if resp.status_code == 200:
            # Return raw PDF bytes
            return Response(content=resp.content, media_type="application/pdf")
        else:
            raise HTTPException(status_code=500, detail=f"PDF Service failed: {resp.text}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))