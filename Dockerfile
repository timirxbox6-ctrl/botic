# Используем легкий Python 3.11
FROM python:3.11-slim

# Создаем рабочую папку внутри контейнера
WORKDIR /app

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код бота в контейнер
COPY . .

# Создаем папку для базы данных (для сохранения)
RUN mkdir -p /data

# Команда запуска бота
CMD ["python", "main.py"]
