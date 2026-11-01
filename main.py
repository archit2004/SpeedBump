import os
import threading
import warnings

import cv2
import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
from transformers import pipeline
from ultralytics import YOLO

warnings.filterwarnings("ignore")

# -----------------------------------------------------------
# Config
# -----------------------------------------------------------
from secrets_config import ADAFRUIT_USERNAME, ADAFRUIT_KEY
FEED_ID = "speed"
INPUT_FEED = f"{ADAFRUIT_USERNAME}/feeds/{FEED_ID}"
OUTPUT_FEED = f"{ADAFRUIT_USERNAME}/feeds/BumpAngle"

VIDEO_SOURCE = "demo2.mp4"
TARGET_CLASSES = [1, 2, 3, 5, 7]

SAMPLE_RATE = 16000
CHUNK_SIZE = int(SAMPLE_RATE * 1.0)
SIREN_KEYWORDS = ['emergency_vehicle', 'siren', 'ambulance', 'police', 'fire']
SIREN_THRESHOLD = 0.15

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
current_mqtt_value = 0.0
is_siren_detected = False
trigger_publish = False
stop_event = threading.Event()  # tells the audio thread to exit cleanly


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to Adafruit IO!")
        client.subscribe(INPUT_FEED)


def on_message(client, userdata, msg):
    global current_mqtt_value, trigger_publish
    try:
        current_mqtt_value = float(msg.payload.decode())
        trigger_publish = True
        print(f"\n---> [MQTT IN] Received Speed: {current_mqtt_value}")
    except ValueError:
        pass


client = mqtt.Client()
client.username_pw_set(ADAFRUIT_USERNAME, ADAFRUIT_KEY)
client.on_connect = on_connect
client.on_message = on_message


# ---------------------------------------------------------------------------
# Audio siren detection (background thread)
# ---------------------------------------------------------------------------
def audio_listener():
    global is_siren_detected, trigger_publish
    try:
        # Using the cloud model that worked perfectly in debugging
        classifier = pipeline("audio-classification", model="MIT/ast-finetuned-audioset-10-10-0.4593")
        print("Audio Listener Active. Listening to mic...")

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32') as stream:
            while not stop_event.is_set():
                audio_chunk, _ = stream.read(CHUNK_SIZE)
                if stop_event.is_set():
                    break

                waveform = np.squeeze(audio_chunk)
                predictions = classifier(waveform)
                top_pred = predictions[0]
                label = top_pred['label'].lower()
                score = top_pred['score']

                if any(word in label for word in SIREN_KEYWORDS) and score > SIREN_THRESHOLD:
                    if not is_siren_detected:
                        print(f"🚨 [AUDIO] Siren Detected! ({label} - {score:.2f}) - Lowering bump immediately!")
                        is_siren_detected = True
                        trigger_publish = True  # Force main loop to publish 0 degrees right now
                else:
                    if is_siren_detected:
                        print("✅ [AUDIO] Siren passed. Restoring previous state...")
                        is_siren_detected = False
                        trigger_publish = True  # Force main loop to restore bump based on last vehicle
    except Exception as e:
        print(f"❌ [AUDIO ERROR] Listener stopped: {e}")
    finally:
        is_siren_detected = False


# ---------------------------------------------------------------------------
# Bump angle logic
# ---------------------------------------------------------------------------
def compute_output_angle(speed, siren, detected_classes):
    if siren:
        return 0
    if speed < 1:
        return 0
    if 5 in detected_classes or 7 in detected_classes:
        return 90
    if 2 in detected_classes or 3 in detected_classes:
        return 60
    if 1 in detected_classes:
        return 30
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global trigger_publish

    print("1. Connecting to Adafruit IO...")
    client.connect("io.adafruit.com", 1883, 60)
    client.loop_start()

    print("2. Starting audio thread...")
    audio_thread = threading.Thread(target=audio_listener, name="audio_listener")
    audio_thread.start()

    print("3. Loading YOLO model...")
    model = YOLO('yolov8n.pt')
    
    print(f"4. Opening video source: {VIDEO_SOURCE}")
    cap = cv2.VideoCapture(VIDEO_SOURCE)

    if not cap.isOpened():
        print(f"ERROR: Could not open {VIDEO_SOURCE}. Check if the file exists!")

    print("5. Watching Vehicles & Processing Logic...")

    try:
        while cap.isOpened():
            success, frame = cap.read()
            if not success:
                # Video ended, loop back to the beginning if you want continuous testing
                # cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                # continue
                break

            results = model(frame, classes=TARGET_CLASSES, verbose=False)
            annotated_frame = results[0].plot()

            detected_classes = []
            if len(results[0].boxes) > 0:
                detected_classes = results[0].boxes.cls.cpu().numpy()

            # trigger_publish is activated EITHER by MQTT incoming speed OR the Siren state changing
            if trigger_publish:
                output_angle = compute_output_angle(
                    current_mqtt_value, is_siren_detected, detected_classes
                )
                client.publish(OUTPUT_FEED, output_angle)
                print(f"<--- [MQTT OUT] Sent BumpAngle: {output_angle}° (Siren Active: {is_siren_detected})")
                trigger_publish = False

            cv2.imshow("Smart Speed Breaker - Vision Radar", annotated_frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("Shutting down system...")
        stop_event.set()
        audio_thread.join(timeout=10)

        cap.release()
        cv2.destroyAllWindows()
        client.loop_stop()
        client.disconnect()
        print("System shut down securely.")

        if audio_thread.is_alive():
            os._exit(0)


if __name__ == "__main__":
    main()