"""
Mumbo Leads — Email Verifier
FastAPI app that serves:
  - Web UI  (GET /)       upload CSV, run verification, export results
  - Clay API (POST /verify) single email verification endpoint
  - Bulk API (POST /verify-bulk) list of emails
"""

import csv, io, asyncio
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from concurrent.futures import ThreadPoolExecutor
from verifier import verify_email

app = FastAPI(title="Mumbo Leads — Email Verifier")
templates = Jinja2Templates(directory="templates")

executor = ThreadPoolExecutor(max_workers=10)


# ── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/upload")
async def upload_csv(file: UploadFile = File(...), email_column: str = Form(...)):
    """
    Accepts a CSV, verifies every email in email_column,
    streams back NDJSON (one JSON object per line) for live UI updates.
    """
    content = await file.read()
    text    = content.decode("utf-8-sig", errors="replace")
    reader  = csv.DictReader(io.StringIO(text))
    rows    = list(reader)
    fields  = reader.fieldnames or []

    if email_column not in fields:
        return JSONResponse({"error": f"Column '{email_column}' not found. Available: {fields}"}, status_code=400)

    async def generate():
        loop = asyncio.get_event_loop()
        for i, row in enumerate(rows):
            email = row.get(email_column, "").strip()
            if not email:
                result = {"email": "", "status": "skipped", "reason": "empty", "mx": ""}
            else:
                result = await loop.run_in_executor(executor, verify_email, email)

            # Merge original row with verification result
            out_row = {**row, **result}
            import json
            yield json.dumps({"row": out_row, "index": i, "total": len(rows)}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/export")
async def export_csv(request: Request):
    """Accepts verified rows as JSON, returns a filtered CSV download."""
    import json
    body   = await request.json()
    rows   = body.get("rows", [])
    status = body.get("status", "all")   # all / valid / invalid / risky / unknown

    filtered = rows if status == "all" else [r for r in rows if r.get("status") == status]

    if not filtered:
        return JSONResponse({"error": "No rows match that filter"}, status_code=400)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(filtered[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(filtered)

    filename = f"verified_{status}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Clay / API endpoints ──────────────────────────────────────────────────────

@app.post("/verify")
async def verify_single(payload: dict):
    """
    Clay API endpoint.
    Body: {"email": "john@company.com"}
    Returns: {"email": "...", "status": "valid|invalid|risky|unknown", "reason": "...", "mx": "..."}
    """
    email = payload.get("email", "").strip()
    if not email:
        return JSONResponse({"error": "email field required"}, status_code=400)

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, verify_email, email)
    return result


@app.post("/verify-bulk")
async def verify_bulk(payload: dict):
    """
    Bulk API endpoint.
    Body: {"emails": ["a@b.com", "c@d.com"]}
    Returns: list of verification results
    """
    emails = payload.get("emails", [])
    if not emails:
        return JSONResponse({"error": "emails array required"}, status_code=400)

    loop    = asyncio.get_event_loop()
    tasks   = [loop.run_in_executor(executor, verify_email, e) for e in emails]
    results = await asyncio.gather(*tasks)
    return list(results)


@app.get("/health")
async def health():
    return {"status": "ok"}
