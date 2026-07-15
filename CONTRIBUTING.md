# Katkı kuralları

## Branch modeli

    master   → üretim. Sadece develop'tan PR ile merge edilir. Korumalı.
    develop  → entegrasyon. Feature branch'ler buraya PR açar.
    feat/*   → yeni özellik      feat/ledger-core
    fix/*    → hata düzeltme     fix/balance-rounding
    chore/*  → altyapı, bağımlılık
    docs/*   → dokümantasyon

Doğrudan `master`'a push yasak. GitHub'da branch protection aç:
Settings → Branches → Add rule → `master` → "Require a pull request before merging"
ve "Require status checks to pass" → `test`.

## Commit mesajları (Conventional Commits)

    <tip>(<kapsam>): <özet>

Tipler: feat, fix, chore, docs, refactor, test, perf, ci

Örnekler:

    feat(ledger): ters kayıt ile iptal desteği
    fix(ledger): kuruş yuvarlamasında ROUND_HALF_UP kullan
    test(ledger): rastgele 100 işlemde kuruş kaybı testi
    chore(docker): vllm servisini gpu profiline al
    docs(readme): kurulum adımları

Kurallar:
- Özet Türkçe, küçük harfle başlar, nokta yok, 72 karakteri geçmez
- Bir commit bir iş yapar. "wip", "düzeltme", "asdf" gibi mesaj yok
- Gövdede *neden*i anlat, *ne*yi kod zaten söylüyor

## Merge

Feature → develop: **Squash merge**. Geçmiş temiz kalır.
develop → master: **Merge commit**. Sürüm sınırı görünür kalır.
