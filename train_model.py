import numpy as np
import warnings
from sklearn.ensemble import RandomForestClassifier
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

# Отключаем предупреждения
warnings.filterwarnings("ignore")

# 1. Создаем симуляционные (синтетические) данные для обучения (около 1000 кошельков).
# Фичи: [tx_count, tx_per_hour, span_hours, repeat_ratio, small_fraction, large_fraction]
np.random.seed(42)

# Данные для "Ботов" (много транзакций, частые повторения, микро-транзакции)
bot_samples = 500
bot_features = np.column_stack([
    np.random.randint(50, 1000, bot_samples),          # tx_count: очень высокие
    np.random.uniform(20, 100, bot_samples),           # tx_per_hour: частые или burst
    np.random.uniform(0.1, 5, bot_samples),            # span_hours: короткое время
    np.random.uniform(0.6, 1.0, bot_samples),          # repeat_ratio: высокая повторяемость
    np.random.uniform(0.7, 1.0, bot_samples),          # small_fraction: много микротранзакций
    np.random.uniform(0.0, 0.1, bot_samples)           # large_fraction: мало крупных сумм
])
bot_labels = np.ones(bot_samples)  # 1 = Bot

# Данные для "Людей" (разнообразные транзакции, растянуты во времени)
human_samples = 500
human_features = np.column_stack([
    np.random.randint(2, 40, human_samples),           # tx_count: мало транзакций
    np.random.uniform(0.1, 5, human_samples),          # tx_per_hour: редкие
    np.random.uniform(10, 1000, human_samples),        # span_hours: длинные периоды
    np.random.uniform(0.0, 0.3, human_samples),        # repeat_ratio: уникальные суммы
    np.random.uniform(0.0, 0.4, human_samples),        # small_fraction: мало микротранзакций
    np.random.uniform(0.1, 1.0, human_samples)         # large_fraction: встречаются крупные переводы
])
human_labels = np.zeros(human_samples)  # 0 = Human

# Объединяем датасеты
X = np.vstack([bot_features, human_features]).astype(np.float32)
y = np.concatenate([bot_labels, human_labels]).astype(np.int64)

# 2. Обучаем модель Random Forest (Случайный лес - хорошо ловит ботов)
print("Обучаем ИИ модель (RandomForest)...")
model = RandomForestClassifier(n_estimators=50, max_depth=5, random_state=42)
model.fit(X, y)
print("Точность модели на обучающих данных:", model.score(X, y))

# 3. Конвертируем обученную модель в формат ONNX (требование OpenGradient)
# Наши входные данные - это 6 чисел с плавающей запятой на каждый кошелек
initial_type = [('float_input', FloatTensorType([None, 6]))]
onnx_model = convert_sklearn(model, initial_types=initial_type)

# 4. Сохраняем модель в файл
with open("wallet_reputation_model.onnx", "wb") as f:
    f.write(onnx_model.SerializeToString())

print("✅ ГОТОВО! Модель сохранена в файл 'wallet_reputation_model.onnx'.")
print("Теперь вы можете загрузить этот файл на OpenGradient Model Hub!")
