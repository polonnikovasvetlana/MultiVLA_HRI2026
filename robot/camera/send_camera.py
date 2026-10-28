
import cv2
import requests
import time


# =====================================
# CONFIGURATION
# =====================================

SERVER = "http://192.168.50.184:8000/frame"
CAMERA = "/dev/video0"

WIDTH = 1280
HEIGHT = 720
FPS = 30
JPEG_QUALITY = 75


# =====================================
# CAMERA INITIALIZATION
# =====================================

cap = cv2.VideoCapture(CAMERA, cv2.CAP_V4L2)

cap.set(
    cv2.CAP_PROP_FOURCC,
    cv2.VideoWriter_fourcc(*"MJPG")
)

cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
cap.set(cv2.CAP_PROP_FPS, FPS)

if not cap.isOpened():
    raise RuntimeError("Cannot open camera")

print("Camera opened")
print("Resolution:",
      cap.get(cv2.CAP_PROP_FRAME_WIDTH),
      "x",
      cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print("Camera FPS setting:", cap.get(cv2.CAP_PROP_FPS))


# =====================================
# HTTP SESSION
# =====================================

session = requests.Session()

print("Sending camera frames to:", SERVER)

frame_count = 0
stats_start = time.perf_counter()

try:
    while True:

        # Read frame
        read_start = time.perf_counter()

        ret, frame = cap.read()

        read_ms = (
            time.perf_counter() - read_start
        ) * 1000

        if not ret:
            print("Frame read failed")
            time.sleep(0.1)
            continue

        # Encode JPEG
        encode_start = time.perf_counter()

        ok, jpg = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )

        encode_ms = (
            time.perf_counter() - encode_start
        ) * 1000

        if not ok:
            print("JPEG encoding failed")
            continue

        # Send HTTP request
        try:
            http_start = time.perf_counter()

            response = session.post(
                SERVER,
                data=jpg.tobytes(),
                headers={
                    "Content-Type": "image/jpeg"
                },
                timeout=2
            )

            response.raise_for_status()

            http_ms = (
                time.perf_counter() - http_start
            ) * 1000

            frame_count += 1

            # Print every 30 successful frames
            if frame_count % 30 == 0:

                elapsed = (
                    time.perf_counter() - stats_start
                )

                actual_fps = frame_count / elapsed

                print(
                    f"FPS: {actual_fps:.1f} | "
                    f"Read: {read_ms:.1f} ms | "
                    f"Encode: {encode_ms:.1f} ms | "
                    f"HTTP: {http_ms:.1f} ms | "
                    f"JPEG: {len(jpg.tobytes()) / 1024:.0f} KB",
                    flush=True
                )

        except requests.RequestException as e:
            print("Send failed:", e)
            time.sleep(0.5)

except KeyboardInterrupt:
    print("\nStopped by user")

finally:
    cap.release()
    session.close()
    print("Camera released")
