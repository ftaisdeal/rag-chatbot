import os
import logging
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from app.rag import CHROMA_DIR, DATA_DIR, RagService

load_dotenv()

app = FastAPI(title="RAG Chatbot")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

rag_service = RagService(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
APP_API_KEY = os.getenv("APP_API_KEY", "").strip()
MAX_UPLOAD_MB = max(int(os.getenv("MAX_UPLOAD_MB", "2")), 1)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
RATE_LIMIT_PER_MIN = max(int(os.getenv("RATE_LIMIT_PER_MIN", "10")), 1)
PROTECTED_PATH_PREFIXES = ("/chat", "/ingest", "/documents")

logger = logging.getLogger("doc_chat")


class InMemoryRateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits = defaultdict(deque)

    def allow(self, key: str):
        now = time.time()
        window_start = now - 60
        timestamps = self._hits[key]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()
        if len(timestamps) >= self.limit:
            return False
        timestamps.append(now)
        return True


rate_limiter = InMemoryRateLimiter(RATE_LIMIT_PER_MIN)


def is_protected_path(path: str):
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in PROTECTED_PATH_PREFIXES)


def get_client_id(request: Request):
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if is_protected_path(path):
        if APP_API_KEY:
            provided_key = request.headers.get("x-api-key", "")
            if provided_key != APP_API_KEY:
                return JSONResponse({"error": "Unauthorized."}, status_code=401)

        client_id = get_client_id(request)
        if not rate_limiter.allow(f"{client_id}:{path}"):
            return JSONResponse({"error": "Too many requests. Please retry shortly."}, status_code=429)

    return await call_next(request)


def list_documents():
    if not DATA_DIR.exists():
        return []
    documents = []
    for path in DATA_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        documents.append(str(path.relative_to(DATA_DIR)))
    return sorted(documents)


def resolve_document_path(file_path: str):
    candidate = (DATA_DIR / file_path).resolve()
    if DATA_DIR.resolve() not in candidate.parents:
        return None
    if candidate.suffix.lower() not in ALLOWED_EXTENSIONS:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat")
async def chat(payload: dict):
    question = (payload or {}).get("question", "").strip()
    if not question:
        return JSONResponse({"error": "Question is required."}, status_code=400)
    try:
        result = rag_service.ask(question)
    except Exception as exc:
        logger.exception("Chat request failed")
        return JSONResponse({"error": "Chat request failed."}, status_code=500)
    return JSONResponse(result)


@app.post("/ingest")
async def ingest():
    try:
        rag_service.ingest()
    except Exception as exc:
        logger.exception("Index rebuild failed")
        return JSONResponse({"error": "Index rebuild failed."}, status_code=500)
    return JSONResponse({"status": "ok"})


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.get("/documents")
async def documents():
    return JSONResponse({"documents": list_documents()})


@app.post("/documents")
async def upload_document(request: Request, file: UploadFile = File(...)):
    filename = Path(file.filename or "").name
    if not filename:
        return JSONResponse({"error": "Filename is required."}, status_code=400)
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": "Unsupported file type."}, status_code=400)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    destination = DATA_DIR / filename
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES:
                return JSONResponse({"error": f"File too large (max {MAX_UPLOAD_MB} MB)."}, status_code=413)
        except ValueError:
            pass

    total_bytes = 0
    try:
        with destination.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    buffer.close()
                    destination.unlink(missing_ok=True)
                    return JSONResponse({"error": f"File too large (max {MAX_UPLOAD_MB} MB)."}, status_code=413)
                buffer.write(chunk)
    except Exception as exc:
        logger.exception("Upload failed")
        return JSONResponse({"error": "Upload failed."}, status_code=500)
    finally:
        await file.close()
    try:
        rag_service.ingest()
    except Exception as exc:
        logger.exception("Index rebuild after upload failed")
        return JSONResponse(
            {
                "status": "ok",
                "filename": filename,
                "warning": "Uploaded, but index not rebuilt.",
            }
        )
    return JSONResponse({"status": "ok", "filename": filename})


@app.get("/documents/view/{file_path:path}")
async def view_document(file_path: str):
    candidate = resolve_document_path(file_path)
    if not candidate:
        return JSONResponse({"error": "Document not found."}, status_code=404)
    return FileResponse(candidate)




@app.delete("/documents/{file_path:path}")
async def delete_document(file_path: str):
    candidate = resolve_document_path(file_path)
    if not candidate:
        return JSONResponse({"error": "Document not found."}, status_code=404)
    try:
        candidate.unlink()
    except Exception as exc:
        logger.exception("Delete failed")
        return JSONResponse({"error": "Delete failed."}, status_code=500)
    try:
        rag_service.ingest()
    except Exception as exc:
        logger.exception("Index rebuild after delete failed")
        return JSONResponse(
            {
                "status": "ok",
                "filename": candidate.name,
                "warning": "Deleted, but index not rebuilt.",
            }
        )
    return JSONResponse({"status": "ok", "filename": candidate.name})
