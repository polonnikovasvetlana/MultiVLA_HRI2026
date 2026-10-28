# Сервер AgenticBotIW

Серверная часть находится в `server/`, актуальный клиент — в `client/`.
Команды ниже выполняются из `server/`, кроме явно указанных переходов.
Пути `/path/to/…` — примеры, их необходимо заменить. Разделы о Linux, ROS
и Conda описывают прежнее окружение; запуск на macOS пока не проверен.

Безопасный голосовой контур для выбора заранее разрешённых робототехнических
навыков. LLM не управляет суставами и не генерирует траектории: она возвращает
строго проверяемый `TaskPlan`, а движение исполняет проверяемый SO-101 runtime.

## Архитектура

```text
ЛОКАЛЬНЫЙ КОМПЬЮТЕР
microphone -> voice_client.py -> WAV по HTTP

СЕРВЕР
stt_server.py (Whisper)
  -> GET executor /objects
  -> planner.api (SQLite + Pydantic + optional LLM)
  -> executor /execute
  -> /agent/robot_command -> SO101Arm
```

Локальный клиент знает только адрес `/transcribe`. Доступ к `/objects`, planner,
SQLite, LLM и executor выполняется сервером. `stop` на сервере обходит inventory,
planner и `AUTO_EXECUTE`, после чего сразу отправляется на `/stop` исполнителя.

## 1. Запуск Whisper

Для русской речи используйте multilingual-модель без суффикса `.en`:

```bash
cd /path/to/AgenticBotIW/server
PLANNER_URL=http://127.0.0.1:8010/plan \
EXECUTOR_URL=http://127.0.0.1:8020 \
AUTO_EXECUTE=1 \
python3 stt_server.py --host 127.0.0.1 --port 8000 --model medium --language ru
```

Проверка:

```bash
curl http://127.0.0.1:8000/health
```

Для приватного доступа локального клиента через tailnet, оставляя Python на
`127.0.0.1`:

```bash
sudo tailscale serve --bg 8000
```

## 2. Запуск планировщика

Без переменных `LLM_*` работает ограниченный детерминированный fallback для
`pick`, `place`, `pick_and_place`, `move_to`, `home` и `stop`:

```bash
python3 -m planner.api --host 127.0.0.1 --port 8010
curl http://127.0.0.1:8010/health
```

Для локального Ollama с отключённым reasoning (планировщику нужен короткий JSON,
а не цепочка рассуждений):

```bash
ollama pull qwen3-vl:8b
export LLM_BASE_URL=http://127.0.0.1:11434/v1
export LLM_MODEL=qwen3-vl:8b
export LLM_API_KEY=ollama
export LLM_REASONING_EFFORT=none
export LLM_MAX_TOKENS=800
python3 -m planner.api --host 127.0.0.1 --port 8010
```

Если Ollama недоступен или два ответа модели подряд не содержат валидный JSON,
planner использует ограниченный детерминированный parser и затем выполняет те
же проверки Pydantic, scene inventory и SQLite-правил.

Пример запроса:

```bash
curl -X POST http://127.0.0.1:8010/plan \
  -H 'Content-Type: application/json' \
  --data '{"text":"возьми красную кружку и поставь на синий поднос","available_objects":["red_cup","blue_tray"]}'
```

SQLite создаётся автоматически в `planner/data/planner.db`. Исходная схема и
начальные навыки находятся в `planner/data/schema.sql`.

## 3. Безопасный dry-run

До подключения робота запускайте исполнитель без физического движения:

```bash
AVAILABLE_OBJECTS=blue_mouse,blue_traffic_light \
python3 -m planner.executor_api --host 127.0.0.1 --port 8020
```

## 4. Голосовой клиент

Актуальный клиент находится в соседней папке `../client/voice_client.py`.
Устаревший клиент из серверной папки удалён по подтверждению владельца.
Инструкции: [клиент](../client/README_CLIENT.md).

Клиент может публиковать готовый preview в локальный ROS-мост при
`AUTO_EXECUTE_DISABLED`. Поэтому `AUTO_EXECUTE=0` на сервере не является
общим выключателем публикации задач. Подробности — в корневом README.

## 5. Текущий SO-101 ROS-контур

Присланный текущий интерфейс использует:

```text
/detected_objects          vision_msgs/Detection2DArray
/agent/robot_command       std_msgs/String с валидированным JSON
/robot_executor/status     std_msgs/String с JSON-статусом
```

Вместо одноразового `fake_agent` используется
`ros_nodes/so101_agent_bridge.py`. Он:

- публикует `/objects` для планировщика;
- принимает высокоуровневый `TaskPlan` по HTTP;
- собирает пять последовательных CV-измерений;
- проверяет confidence и разброс координат;
- создаёт только разрешённые robot steps;
- публикует итоговую задачу в `/agent/robot_command`.

Запуск на машине с ROS и `so101_control`:

```bash
source /opt/ros/humble/setup.bash
source /PATH/TO/SO101_OVERLAY/install/setup.bash
cd /path/to/AgenticBotIW/server

python3 -m ros_nodes.robot_executor \
  --ros-args --params-file config/so101.yaml
```

В другом терминале с теми же ROS environment variables:

```bash
python3 -m ros_nodes.so101_agent_bridge \
  --host 127.0.0.1 --port 8020 \
  --ros-args --params-file config/so101.yaml
```

Координаты передаются без дополнительных сдвигов:

- `frame=camera` — executor использует координаты CV без изменений;
- `frame=robot` — executor использует абсолютные координаты без изменений;
- home отправляется как реальная поза `(-78.7, 6.7, 474.7)` в `robot` frame.

Параметры находятся в `config/so101.yaml`. До первого запуска на железе нужно
сверить workspace, lift и home с фактической калибровкой.

В текущем системном `/usr/bin/python3` обнаружены ROS Humble, `rclpy`,
`std_msgs` и `vision_msgs`. Аппаратные ROS-ноды специально не зависят от
Pydantic и запускаются системным Python 3.10. Модуль `so101_control` в этом
checkout и несourced окружении не найден — перед запуском executor нужно
подключить overlay, из которого работал исходный `SO101Arm`.

Planner сохраняет отдельную строгую Pydantic-валидацию. Найденное Conda-
окружение `/media/sa/Disk2/sys/conda_envs/agenticbotiw` содержит `requests` и
`SpeechRecognition`, но пока не содержит `pydantic`. Не запускайте ROS Humble
из текущего `(base)` Python 3.13: используйте системный Python для ROS-ноды, а
выделенное Conda-окружение — для Whisper, voice client и planner после
установки туда Pydantic 2.

## 6. Альтернативный BehaviorTree.CPP-контур

Пакет находится в `robot_ws/src/voice_task_executor`. Он предоставляет action
`ExecuteTask`, загружает XML по имени разрешённого поведения и подключает
реальные BT-ноды через параметр `bt_plugin_libraries`.

```bash
cd robot_ws
colcon build --packages-select voice_task_executor
source install/setup.bash
ros2 run voice_task_executor task_executor_node \
  --ros-args -p bt_plugin_libraries:="[your_moveit_bt_nodes.so]"
```

HTTP-мост вместо dry-run:

```bash
source robot_ws/install/setup.bash
python3 -m planner.ros_bridge --host 127.0.0.1 --port 8020
```

XML-деревья требуют плагин с узлами `CheckRobotReady`, `BuildPose`, `ValidatePose`,
`MoveItPlan`, `MoveItExecute`, `DetectObject`, `CloseGripper` и остальными
именами из `behavior_trees/*.xml`. Если плагин не передан или узел не
зарегистрирован, ROS action завершится с `BT_CONFIGURATION_ERROR` и никакого
движения не произойдёт.

Конкретные MoveIt-ноды зависят от URDF/SRDF, planning group, контроллера
захвата, perception topics и MoveIt config конкретного робота. Поэтому они не
подменены фиктивной успешной реализацией.

## Тесты

```bash
python3 -m unittest discover -s tests -v
```

Проверяются строгая схема, неизвестные поля, обязательные аргументы,
русские алиасы, перевод сантиметров в метры, workspace, scene inventory,
prompt injection, запрос уточнения и `stop` без LLM.

ROS-smoke без физического робота:

```bash
source /opt/ros/humble/setup.bash

# При запущенном so101_agent_bridge на порту 18030:
/usr/bin/python3 tests/ros_so101_smoke.py

# При robot_executor, запущенном с tests/fakes/so101_control.py:
/usr/bin/python3 tests/ros_executor_stop_smoke.py
```

Первый тест проверяет `Detection2DArray -> восемь robot steps`, второй — что
немедленный stop отменяет активную задачу и не позволяет ей продолжить шаги.

## 7. Фейковая CV-нода

Для проверки контура без компьютера с реальной CV используйте отдельный
publisher. Он имитирует стабильные `blue_mouse` и `blue_traffic_light` в
`/detected_objects`, но по умолчанию разоружён:

```bash
source /opt/ros/jazzy/setup.bash
cd /path/to/AgenticBotIW/server

/usr/bin/python3 -m ros_nodes.fake_cv_publisher \
  --ros-args -p publish_enabled:=true
```

Не запускайте fake CV одновременно с реальной CV. При включённом
`AUTO_EXECUTE=1` голосовой план сможет использовать фейковые координаты для
реального движения, поэтому сначала проверяйте его без запущенного
`robot_executor` или с `AUTO_EXECUTE=0`.

Проверка:

```bash
ros2 topic echo /detected_objects --once
curl http://127.0.0.1:8020/objects
```

Ожидаемый inventory: `blue_mouse` и `blue_traffic_light`.
