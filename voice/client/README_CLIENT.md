# Запуск клиента

Актуальный клиент: `client/voice_client.py`.
Распознавание и планирование выполняются в `../server/`.
Синтез речи также остаётся серверным сервисом.

> Ниже сохранена инструкция прежнего Linux/ROS-окружения. Серверные
> адреса, пути и ROS_DOMAIN_ID нужно сверить; это не инструкция запуска
> на Mac. В конце исходной инструкции встречается другой ROS_DOMAIN_ID.
> Не выбирайте значение автоматически.

Для локальных STT/TTS клиент использует по умолчанию
`http://127.0.0.1:8000/transcribe` и `http://127.0.0.1:8030/synthesize`.
Мост задаётся через ROBOT_BRIDGE_URL, по умолчанию `http://127.0.0.1:8020`.
Клиент автоматически вызывает `/publish` для готового preview при
`AUTO_EXECUTE_DISABLED`; не подключайте аппаратный исполнитель для
обычной проверки микрофона.


## 1. SO-101 HTTP bridge

```bash
cd /home/sa/personal_projects/AgenticBotIW/client
source /opt/ros/jazzy/setup.zsh
export ROS_DOMAIN_ID=43

/usr/bin/python3 -m ros_nodes.so101_agent_bridge \
  --host 0.0.0.0 \
  --port 8020 \
  --ros-args --params-file config/so101.yaml
```

## 2. Fake CV

В новом терминале:

```bash
cd /home/sa/personal_projects/AgenticBotIW/client
source /opt/ros/jazzy/setup.zsh
export ROS_DOMAIN_ID=43

/usr/bin/python3 -m ros_nodes.fake_cv_publisher \
  --ros-args -p publish_enabled:=true
```

## 3. Проверка объектов

```bash
curl -s http://127.0.0.1:8020/health | /usr/bin/python3 -m json.tool
curl -s http://127.0.0.1:8020/objects
```

Ожидаемый ответ:

```json
{"objects": ["blue_mouse", "blue_traffic_light"]}
```

## 4. Голосовой клиент

В новом терминале:

```bash
cd /home/sa/personal_projects/AgenticBotIW/client
conda activate AgenticBotIW

STT_SERVER_URL="http://100.103.57.45:8000/transcribe" \
TTS_SERVER_URL="http://100.103.57.45:8030/synthesize" \
python voice_client.py --speak-responses \
  --show-all-text
```

Готовый preview автоматически отправляется в локальный bridge и сразу
публикуется в ROS-топик:

```text
/voice/robot_command
```

На компьютере с CV и executor должны использоваться тот же `ROS_DOMAIN_ID=43`
и обновлённый `robot_executor.py`.

Проверка топика на компьютере с executor:

```bash
source /opt/ros/jazzy/setup.zsh
export ROS_DOMAIN_ID=43
ros2 topic echo /voice/robot_command std_msgs/msg/String
```

## Остановка

Нажмите `Ctrl+C` в каждом терминале.


pkill -f ros_nodes.so101_agent_bridge

cd /home/sa/personal_projects/AgenticBotIW/client
source /opt/ros/jazzy/setup.zsh
export ROS_DOMAIN_ID=42

/usr/bin/python3 -m ros_nodes.so101_agent_bridge \
  --host 0.0.0.0 \
  --port 8020 \
  --ros-args --params-file config/so101.yaml