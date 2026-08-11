"""vLLM cihazının (Bosna, 2080 Super) uzaktan açma/kapama tercihi — "Yol B".

Aynı "panel İSTER, host UYGULAR" mimarisi CLAUDE.md > "Panelden geri
yükleme — 'Yol A'" bölümünde geri yükleme için kullanılıyordu; burada aynı
desen GPU açma/kapamaya uygulanıyor. Panel İzmir'de çalışır, vLLM Bosna'daki
host'ta Docker container olarak çalışır. Panel Bosna'ya DOĞRUDAN KOMUT
GÖNDERMEZ (SSH/uzaktan çalıştırma yok) — yalnızca DB'ye bir TERCİH yazar
(settings.vllm_desired). Bosna'daki host script'i (scripts/vllm-control.sh,
systemd timer ile ~30 sn'de bir) bu tercihi KENDİSİ ÇEKER (GET
/api/vllm-desired, bkz. app/api/routes.py) ve uygular (docker start/stop).

Bu tek yönlü "pull" mimarisi kasıtlı: İzmir'den Bosna'ya açık bir port/komut
kanalı YOK — İzmir ele geçirilse bile Bosna'ya rastgele komut çalıştıramaz,
yalnızca bu iki değerden birini (on/off) okutabilir. Güvenlik yüzeyi küçük.

Uygulanma ~30 saniye gecikmelidir (script'in poll aralığı) — panel bu yüzden
"istenen" (DB'deki tercih) ile "gerçek" (vLLM şu an gerçekten cevap veriyor
mu, bkz. llm_provider.vllm_healthy) durumunu AYRI gösterir; ikisi arasında
fark varsa "başlatılıyor/kapatılıyor" sayılır (bkz. is_pending).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting

VLLM_DESIRED_KEY = "vllm_desired"
VLLM_DESIRED_VALUES = ("on", "off")
# Güvenli başlangıç: hiç ayarlanmamışsa GPU boş kalır, yanlışlıkla açılıp
# VRAM dolmaz. Açmak her zaman kasıtlı bir panel tıklaması gerektirir.
VLLM_DESIRED_DEFAULT = "off"


async def get_vllm_desired(session: AsyncSession) -> str:
    """DB'deki tercihi okur. Hiç ayarlanmamışsa ya da tanınmayan (bozuk/eski)
    bir değerse "off" sayılır — llm_provider.get_llm_primary ile aynı
    "bozuk veri güvenli varsayılana düşer" ilkesi."""
    row = await session.get(Setting, VLLM_DESIRED_KEY)
    value = row.value if row is not None else None
    return value if value in VLLM_DESIRED_VALUES else VLLM_DESIRED_DEFAULT


async def set_vllm_desired(session: AsyncSession, value: str) -> None:
    if value not in VLLM_DESIRED_VALUES:
        raise ValueError(f"Geçersiz vllm_desired değeri: {value!r}")
    setting = await session.get(Setting, VLLM_DESIRED_KEY)
    if setting is None:
        session.add(Setting(key=VLLM_DESIRED_KEY, value=value))
    else:
        setting.value = value
    await session.flush()


def is_pending(desired: str, reachable: bool) -> bool:
    """İstenen durumla gerçek durum arasında bir geçiş sürüyor mu (Bosna
    script'i henüz uygulamamış, ~30 sn gecikme payı). "on" istenip henüz
    erişilemiyorsa (VRAM'e yükleniyor) ya da "off" istenip hâlâ
    erişilebiliyorsa (host henüz durdurmadı) True — panel bunu
    "başlatılıyor/kapatılıyor ~30sn" olarak gösterir. Saf fonksiyon (ağa/DB'ye
    çıkmaz) ki hem admin uç noktası hem ileride başka bir görünüm aynı
    mantığı paylaşsın (bkz. llm_provider.select_source ile aynı desen)."""
    return (desired == "on") != reachable
