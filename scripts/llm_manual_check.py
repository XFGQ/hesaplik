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

# Bug (2026-07-26, prompt): LLM "borçlu" kelimesini görünce tutar/kayıt
# fiili yokken bile bunu bir borç KAYDI sanıyordu (kind=debt, amount=None,
# district="ahmetbey" gibi yanlış kısaltılmış bir ilçe). Bu üçü kesinlikle
# balance_query olmalı, amount=None, district (varsa) TAM ilçe adı olmalı
# ("ahmetbeyler", "ahmetbey" DEĞİL). Kişi mehmet/ali dev DB'de olmayabilir,
# bu yüzden bunlar tam pipeline yerine doğrudan provider ile kontrol edilir
# — burada ölçülen şey ledger değil, LLM'in ne çıkardığı.
BAKIYE_SORGUSU_CUMLELERI = [
    ("ahmetbeylerden mehmet ne kadar borçlu", "balance_query", "mehmet", "ahmetbeyler"),
    ("mehmet borcu ne kadar", "balance_query", "mehmet", None),
    ("ali ne kadar borçlu", "balance_query", "ali", None),
]


async def _check_prompt_fix(provider) -> None:
    print("\n=== Prompt düzeltmesi: bakiye sorgusu vs kayıt ===")
    for text, expected_kind, expected_person, expected_district in BAKIYE_SORGUSU_CUMLELERI:
        intent = await provider.parse(text)
        print(f"\n--- {text!r} ---")
        print("intent  :", intent)
        if intent is None:
            print("SONUÇ   : BAŞARISIZ (None döndü)")
            continue
        ok = (
            intent.kind == expected_kind
            and intent.person_name == expected_person
            and intent.amount is None
            and intent.district == expected_district
        )
        print(
            "beklenen:",
            {"kind": expected_kind, "person_name": expected_person,
             "amount": None, "district": expected_district},
        )
        print("SONUÇ   :", "DOĞRU" if ok else "YANLIŞ")


async def main() -> None:
    print(f"LLM_PROVIDER={settings.llm_provider} OLLAMA_URL={settings.ollama_url} "
          f"LLM_MODEL={settings.llm_model} LLM_TIMEOUT={settings.llm_timeout}")
    provider = llm_provider.get_provider()
    if provider is None:
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

    await _check_prompt_fix(provider)


if __name__ == "__main__":
    asyncio.run(main())
