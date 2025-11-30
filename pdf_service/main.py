from fastapi import FastAPI, Response
from pydantic import BaseModel
from weasyprint import HTML

app = FastAPI()

class PdfRequest(BaseModel):
    contents: str

@app.post("/")
def generate_pdf(req: PdfRequest):
    # This runs the conversion inside the container
    pdf_bytes = HTML(string=req.contents).write_pdf()
    return Response(content=pdf_bytes, media_type="application/pdf")