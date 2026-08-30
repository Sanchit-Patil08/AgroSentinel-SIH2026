import kagglehub
import pandas as pd
import pathlib
import numpy as np

from sklearn.model_selection import train_test_split
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

p = kagglehub.dataset_download(
    "datasetengineer/crop-health-and-environmental-stress-dataset"
)

f = list(pathlib.Path(p).rglob("*.csv"))[0]
df = pd.read_csv(f)

features = [
    "NDVI",
    "SAVI",
    "Chlorophyll_Content",
    "Leaf_Area_Index",
    "Temperature",
    "Humidity",
    "Rainfall",
    "Wind_Speed",
    "Soil_Moisture",
    "Soil_pH",
    "Organic_Matter",
    "Pest_Hotspots",
    "Weed_Coverage",
    "Pest_Damage",
    "Expected_Yield",
    "Water_Flow",
    "Drainage_Features"
]

target = "Crop_Stress_Indicator"

X = df[features].apply(pd.to_numeric, errors="coerce")
y = df[target]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

model = HistGradientBoostingRegressor(
    max_iter=200,
    learning_rate=0.08,
    max_leaf_nodes=31,
    random_state=42
)

model.fit(X_train, y_train)

pred = model.predict(X_test)

print("\n=== DATASET ML TEST ===")
print("Rows:", len(df))
print("Features:", len(features))
print("Train rows:", len(X_train))
print("Test rows:", len(X_test))

print("\nMAE:", round(mean_absolute_error(y_test, pred), 3))
print("R²:", round(r2_score(y_test, pred), 3))

print("\nActual vs Predicted:")
for actual, predicted in zip(y_test.head(10), pred[:10]):
    print(f"Actual: {actual:5.1f} | Predicted: {predicted:5.1f}")