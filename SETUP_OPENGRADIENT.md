# 🔑 Настройка OpenGradient для Vercel

## Шаг 1: Получить PRIVATE_KEY от OpenGradient

1. Зайдите в Discord OpenGradient: https://linktr.ee/opengradient
2. Создайте кошелек в сети OpenGradient
3. Экспортируйте PRIVATE_KEY

## Шаг 2: Добавить переменные в Vercel

1. Зайдите в проект на Vercel: https://vercel.com/dashboard
2. Выберите проект `OpenGradient_Reputation`
3. Перейдите в **Settings** → **Environment Variables**
4. Добавьте 2 переменные:

```
PRIVATE_KEY=ваш_приватный_ключ_от_opengradient
OPENGRADIENT_MODEL_ID=4-vJc69O2zGJTG
```

5. Нажмите **Save**

## Шаг 3: Redeploy

1. После добавления переменных нажмите **Redeploy**
2. Или запушьте новые изменения в GitHub

## Проверка

После deploy откройте консоль браузера (F12) и проверьте:
- Запрос к `/api/txs?address=0x...` должен вернуть `assessment: "Human"` или `"Bot"`
- В `reasons` должно быть: `"Verified by OpenGradient AI (Model 4-vJc69O2zGJTG)"`

## 🔍 Тестовые адреса

Можно проверить на любом кошельке в Base Sepolia:
- `0x1234567890123456789012345678901234567890` (пустой)
- Найдите активный кошелек в Base Sepolia explorer

---

**Примечание:** Без PRIVATE_KEY работает режим симуляции (эвристики вместо ИИ).
