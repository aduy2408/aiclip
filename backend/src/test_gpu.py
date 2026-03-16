import mediapipe as mp

model_path = "../models/blaze_face_short_range.tflite"
try:
    options = mp.tasks.vision.FaceDetectorOptions(
        base_options=mp.tasks.BaseOptions(
            model_asset_path=model_path,
            delegate=mp.tasks.BaseOptions.Delegate.GPU
        ),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        min_detection_confidence=0.5
    )
    with mp.tasks.vision.FaceDetector.create_from_options(options) as detector:
        print("GPU Delegate loaded successfully!")
except Exception as e:
    print("Failed to load GPU Delegate:", e)
