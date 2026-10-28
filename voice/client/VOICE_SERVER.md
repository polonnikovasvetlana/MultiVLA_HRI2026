# Исторический сценарий: VAD-клиент → отдельный Whisper-сервер

Упрощённый сервер перенесён в `../experiments/standalone_stt_server.py`.
Он распознаёт речь и проверяет слово активации, но не вызывает planner
и не возвращает полноценный pipeline основного сервера.
Актуальный клиент ожидает pipeline, поэтому этот сценарий не является
полной рабочей заменой связки `client/` и `server/`.

Ниже сохранены исторические заметки, а не проверенная инструкция для Mac.
Команды сервера выполняются из `experiments/`, клиента и ROS-нод — из
`client/`. Старые адреса и ROS_DOMAIN_ID требуют проверки.


Пайплайн состоит из двух процессов:

1. `voice_client.py` лёгким VAD ожидает речь и записывает её до паузы.
2. WAV отправляется в `standalone_stt_server.py`; на клиенте нет Whisper, Torch и
   другой тяжёлой модели.
3. Сервер распознаёт фразу и возвращает `command` только если нашёл wake word.
   Без wake word ответ: `{"activated": false}`.

VAD определяет только наличие речи. Само wake word проверяется после транскрипции
на сервере.

## Зависимости

Клиент (Ubuntu/Debian):

```bash
sudo apt install portaudio19-dev python3-dev
python -m pip install requests SpeechRecognition PyAudio
```

Сервер:

```bash
sudo apt install ffmpeg
python -m pip install openai-whisper
```

Для CUDA установите подходящую сборку PyTorch до `openai-whisper`.

## Запуск

В первом терминале:

```bash
conda activate AgenticBotIW
cd /path/to/AgenticBotIW/experiments
python standalone_stt_server.py
```

Если команды произносятся по-английски:

```bash
python standalone_stt_server.py --language en
```

Для смешанной русско-английской речи можно включить автоопределение:

```bash
python standalone_stt_server.py --language auto
```

Во втором:

```bash
conda activate AgenticBotIW
cd /path/to/AgenticBotIW/client
python voice_client.py
```

Фраза для проверки: `Вэлли, какая сейчас погода?`. Клиент получит команду
`какая сейчас погода` — wake word из неё удаляется.

По умолчанию сервер использует Whisper `small`. Модель можно переопределить:

```bash
python standalone_stt_server.py --model medium
# или
WHISPER_MODEL=medium python standalone_stt_server.py
```

## Другое wake word

Wake word теперь настраивается на сервере:

```bash
WAKE_WORDS="джарвис,jarvis" python standalone_stt_server.py
# или
python standalone_stt_server.py --wake-words "джарвис,jarvis"
```

По умолчанию: `вэлли,валли,Vallie,Wally,Valley`.

## Тюнинг VAD

Если клиент ловит шум, увеличьте порог:

```bash
ENERGY_THRESHOLD=400 python voice_client.py
```

Если обрезает паузы внутри фразы:

```bash
PAUSE_THRESHOLD=1.2 python voice_client.py
```

Доступны также `VOICE_WAIT_TIMEOUT`, `MAX_PHRASE_SECONDS`, `AMBIENT_NOISE_SECONDS`
и `MIC_INDEX`.

## Сервер на другом компьютере

На сервере:

```bash
python standalone_stt_server.py --host 0.0.0.0 --port 8000
```

На клиенте:

```bash
STT_SERVER_URL="http://192.168.1.50:8000/transcribe" python voice_client.py
```

Проверка:

```bash
curl http://192.168.1.50:8000/health
```

Порт `8000` должен быть доступен клиенту. Прототип не имеет аутентификации,
поэтому не выставляйте его в публичный интернет.

## Fake CV и SO-101 HTTP bridge

`fake_cv_publisher` и `so101_agent_bridge` обязаны работать в одном
`ROS_DOMAIN_ID` с остальными ROS-узлами. На текущей SO-101 установке используется
domain `42`. Запускайте оба процесса так:

```bash
source /opt/ros/jazzy/setup.zsh
export ROS_DOMAIN_ID=42
python3 -m ros_nodes.so101_agent_bridge \
  --host 0.0.0.0 --port 8020 \
  --ros-args --params-file config/so101.yaml
```

Во втором терминале:

```bash
source /opt/ros/jazzy/setup.zsh
export ROS_DOMAIN_ID=42
python3 -m ros_nodes.fake_cv_publisher \
  --ros-args -p publish_enabled:=true
```

Проверка perception без отправки команды роботу:

```bash
curl -s http://127.0.0.1:8020/health | python3 -m json.tool
curl -s http://127.0.0.1:8020/objects
```

В `/health` поле `ros_domain_id` должно быть `42`, счётчик
`messages_received` должен расти, а `/objects` должен вернуть `blue_mouse` и
`blue_traffic_light`. Fake CV только публикует perception и сам не отправляет
команды роботу, однако подключённый planner может использовать эти объекты для
исполнения голосовой команды.
