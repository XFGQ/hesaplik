"""Faz 4 LLM fallback'ini GERÇEK Ollama ile elle doğrulamak için script.

`just test`'in parçası DEĞİLDİR ve pytest tarafından toplanmaz (dosya adı
test_*.py değil) — gerçek Ollama'nın (LLM_PROVIDER=ollama, OLLAMA_URL,
LLM_MODEL .env'de tanımlı) localhost'ta çalışıyor olmasını gerektirir.

Kullanım:
    just db   # veritabanı ayakta olsun
    .venv/bin/python scripts/llm_manual_check.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import RawMessage
from app.services import llm_provider, message_processor

CUMLELER = [
    # Kural parser çözemez (ne debt/payment/balance/list kalıbına uymuyor,
    # "söyle" bir liste fiili değil) -> LLM'e düşmesi gerekir.
    "kişileri söyle",
    # Bug (2026-07-26): "borcunu" bir bakiye anahtar kelimesi olduğu için
    # kural parser bunu yanlışlıkla sahte bir bakiye sorgusuna
    # dönüştürüyordu ve LLM'e hiç düşmüyordu. Artık kural parser None
    # dönmeli, LLM devreye girip bunu TAHSİLAT olarak çözmeli.
    "ahmet yılmaz 20 balya borcunu 15000 tl ödedi",
]


async def main() -> None:
    print(f"LLM_PROVIDER={settings.llm_provider} OLLAMA_URL={settings.ollama_url} "
          f"LLM_MODEL={settings.llm_model}")
    if llm_provider.get_provider() is None:
        print("UYARI: LLM devre dışı (LLM_PROVIDER=none). .env'de "
              "LLM_PROVIDER=ollama ayarlayın ve Ollama'yı başlatın.")
        return

    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        for i, text in enumerate(CUMLELER):
            raw = RawMessage(
                channel="manual-check", external_id=f"manual-{i}", chat_id="0",
                payload={"text": text},
            )
            session.add(raw)
            await session.flush()

            result = await message_processor.process_raw_message(session, raw, text)

            print(f"\n--- {text!r} ---")
            print("outcome :", result.outcome.value)
            print("resolved:", result.resolved)
        # Deftere hiçbir şey yazılmasın diye kaydetmiyoruz.
        await session.rollback()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
