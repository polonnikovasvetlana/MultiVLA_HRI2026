# Запуск AgenticBotIW на сервере

> Историческая инструкция для Linux/Conda/Tailscale. Адреса и окружения
> требуют проверки. Пути обновлены с учётом папки `server/`, но перенос
> на прежней машине не проверен. Для структуры проекта см. [README](../README.md).

## Адреса

```text
Сервер:          100.103.57.45
ROS-мост:        100.102.232.26:8020
Planner API:     127.0.0.1:8010
STT API:         0.0.0.0:8000
TTS API:         0.0.0.0:8030
Ollama API:      127.0.0.1:11434
```

## 1. Перейти в проект

```bash
cd /media/sa/Disk2/sa-work/AgenticBotIW/server
```

Python окружения:

```bash
export AGENTICBOT_PYTHON=/media/sa/Disk2/sys/conda_envs/agenticbotiw/bin/python
```

## 2. Проверить ROS-мост

ROS-мост запускается на другом компьютере. Сервер должен видеть его по HTTP:

```bash
curl -s http://100.102.232.26:8020/health | python3 -m json.tool
curl -s http://100.102.232.26:8020/objects | python3 -m json.tool
```

В `/objects` должны находиться видимые CV-объекты, например:

```json
{
  "objects": ["blue_mouse", "blue_traffic_light"]
}
```

## 3. Запустить Ollama

Проверка уже запущенного Ollama:

```bash
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
```

Если Ollama не запущен:

```bash
ollama serve
```

Однократная загрузка модели:

```bash
ollama pull qwen3-vl:8b
```

## 4. Запустить planner

В отдельном терминале:

```bash
cd /media/sa/Disk2/sa-work/AgenticBotIW/server

LLM_BASE_URL=http://127.0.0.1:11434/v1 \
LLM_MODEL=qwen3-vl:8b \
LLM_API_KEY=ollama \
LLM_REASONING_EFFORT=none \
LLM_MAX_TOKENS=800 \
/media/sa/Disk2/sys/conda_envs/agenticbotiw/bin/python -m planner.api \
  --host 127.0.0.1 \
  --port 8010
```

Проверка:

```bash
curl -s http://127.0.0.1:8010/health | python3 -m json.tool
```

Ожидаемый режим:

```json
{
  "status": "ok",
  "llm_configured": true,
  "mode": "llm"
}
```

## 5. Запустить Silero TTS

Однократно установить зависимость в выделенное окружение:

```bash
/home/sa/miniconda3/bin/conda install -y \
  -p /media/sa/Disk2/sys/conda_envs/agenticbotiw \
  -c conda-forge omegaconf
```

В отдельном терминале:

```bash
cd /media/sa/Disk2/sa-work/AgenticBotIW/server

/media/sa/Disk2/sys/conda_envs/agenticbotiw/bin/python tts_server.py \
  --host 0.0.0.0 \
  --port 8030 \
  --speaker en_23 \
  --sample-rate 48000
```

При первом запуске Torch Hub скачивает Silero `v3_en`; следующие запуски
используют локальный cache.

Проверка:

```bash
curl -s http://127.0.0.1:8030/health | python3 -m json.tool
```

## 6. Запустить STT-сервер без движения робота

В отдельном терминале:

```bash
cd /media/sa/Disk2/sa-work/AgenticBotIW/server

PLANNER_URL=http://127.0.0.1:8010/plan \
EXECUTOR_URL=http://100.102.232.26:8020 \
AUTO_EXECUTE=0 \
/media/sa/Disk2/sys/conda_envs/agenticbotiw/bin/python stt_server.py \
  --host 0.0.0.0 \
  --port 8000 \
  --model large-v3 \
  --language en
```

В этом режиме сервер получает объекты и координаты CV, строит preview действий,
но не публикует задание на движение робота.

Проверка:

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

## 7. Запустить STT-сервер с исполнением

Остановить текущий STT-сервер через `Ctrl+C` и запустить:

```bash
cd /media/sa/Disk2/sa-work/AgenticBotIW/server

PLANNER_URL=http://127.0.0.1:8010/plan \
EXECUTOR_URL=http://100.102.232.26:8020 \
AUTO_EXECUTE=1 \
/media/sa/Disk2/sys/conda_envs/agenticbotiw/bin/python stt_server.py \
  --host 0.0.0.0 \
  --port 8000 \
  --model medium.en \
  --language en
```

При `AUTO_EXECUTE=1` подтверждённая команда отправляется ROS-мосту, который
публикует последовательность действий в `/agent/robot_command`.

## 8. Итоговая проверка

```bash
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
curl -s http://127.0.0.1:8010/health | python3 -m json.tool
curl -s http://127.0.0.1:8030/health | python3 -m json.tool
curl -s http://100.102.232.26:8020/health | python3 -m json.tool
curl -s http://100.102.232.26:8020/objects | python3 -m json.tool
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

## Остановка

В терминалах `planner`, `tts_server.py`, `stt_server.py` и `ollama serve` нажать:

```text
Ctrl+C
```
