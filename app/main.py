import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings

log = logging.getLogger(__name__)

app = FastAPI(title="Hesaplık", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sıra kritik: önce API (/api/...), sonra statik dosyalar, EN SON SPA
# catch-all. Aksi hâlde catch-all API yollarını yutar.
app.include_router(router)


@app.get("/api/health")
async def health():
    return {"ok": True}


# ----------------------------------------------------------------- web (SPA)
#
# Üretimde tek port: aynı FastAPI hem API'yi hem React build'ini servis eder,
# sunucudaki Nginx tek yere proxy'ler. Geliştirmede bu blok devre dışı kalır
# (web/dist yoksa) — `just web` yine Vite dev sunucusunu 5173'te çalıştırır ve
# /api'yi 8000'e proxy'ler, akış değişmez.


def _dist_dir() -> Path:
    """web/dist'in mutlak yolu. Göreli ayar proje köküne göre çözülür
    (app/main.py -> proje kökü), böylece çalışma dizininden bağımsız."""
    path = Path(settings.web_dist)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path.resolve()


def _mount_spa(fastapi_app: FastAPI) -> None:
    dist = _dist_dir()
    index = dist / "index.html"
    if not index.is_file():
        log.warning(
            "web/dist bulunamadı (%s) — yalnızca API servis edilecek. "
            "Arayüz için: cd web && npm run build",
            dist,
        )
        return

    # Hash'li JS/CSS: StaticFiles doğru Content-Type ve 304'leri kendi verir.
    assets = dist / "assets"
    if assets.is_dir():
        fastapi_app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @fastapi_app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Bilinmeyen /api yolu index.html DÖNMEZ; istemci 200 + HTML alıp
        # JSON sanmasın diye dürüstçe 404.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="bulunamadı")

        # dist içindeki gerçek dosyalar (manifest.webmanifest, icons/...).
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate)

        # Geri kalan her şey React router'ın yolu (/kisiler, /rapor ...).
        # index.html asla önbelleğe alınmasın: yeni deploy'da eski index
        # artık var olmayan asset hash'lerini isterdi.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


_mount_spa(app)
