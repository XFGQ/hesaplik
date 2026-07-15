from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="Hesaplık", version="0.1.0")
app.include_router(router)


@app.get("/api/health")
async def health():
    return {"ok": True}
