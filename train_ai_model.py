import os
import tensorflow as tf
import numpy as np

# Ensure the directory exists
model_dir = r"C:\Zonneveld\AI_Models"
os.makedirs(model_dir, exist_ok=True)  # Create folder if it doesn't exist

# Define model save path
model_path = os.path.join(model_dir, "local_massing_model.h5")

# Example Training Data (Replace with real data)
X_train = np.array([[100, 500, 20], [120, 800, 30], [150, 900, 25]])
y_train = np.array([[105, 520, 22], [125, 850, 33], [160, 950, 27]])

# Build a Simple Neural Network Model
model = tf.keras.Sequential(
    [
        tf.keras.layers.Dense(16, activation="relu", input_shape=(3,)),
        tf.keras.layers.Dense(8, activation="relu"),
        tf.keras.layers.Dense(3),  # Predicts height, footprint, orientation
    ]
)

model.compile(optimizer="adam", loss="mse", metrics=["mae"])

# Train the Model
model.fit(X_train, y_train, epochs=50, batch_size=2, verbose=1)

# Save the Model
model.save(model_path)

print("✅ Model saved successfully at:", model_path)
