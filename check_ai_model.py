import tensorflow as tf

MODEL_PATH = r"C:\Zonneveld\AI_Models\local_massing_model.h5"

model = tf.keras.models.load_model(MODEL_PATH)

print("✅ AI Model Loaded Successfully")
print("📏 Model Input Shape:", model.input_shape)
print("📏 Model Output Shape:", model.output_shape)
