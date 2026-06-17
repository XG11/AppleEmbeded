import cv2
import time

cam_id = 0
cap = cv2.VideoCapture(cam_id)

if not cap.isOpened():
    raise RuntimeError(f"Could not open camera /dev/video{cam_id}")

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)

count = 0
start = time.time()

while count < 300:
    ok, frame = cap.read()
    if not ok:
        print("Frame read failed")
        break

    if count == 0:
        cv2.imwrite("digit_test_frame.jpg", frame)

    count += 1

cap.release()

elapsed = time.time() - start
print(f"frames={count}, elapsed={elapsed:.2f}s, fps={count/elapsed:.2f}")
print("Saved first frame to digit_test_frame.jpg")