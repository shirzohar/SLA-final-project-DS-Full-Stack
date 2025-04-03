import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.model_selection import train_test_split, RandomizedSearchCV, KFold
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import lightgbm as lgb  # Importing LightGBM
from app.db import get_collection
from dotenv import load_dotenv
from sklearn.utils import shuffle

import warnings

warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)
warnings.simplefilter(action='ignore', category=pd.errors.SettingWithCopyWarning)

load_dotenv()
DATABASE_NAME = os.getenv("DATABASE_NAME")
MODEL_PATH = "app/services/lightgbm_duration_model.pkl"  # Update model path
ENCODER_DIR = "app/services/encoders_duration"
os.makedirs(ENCODER_DIR, exist_ok=True)

ENCODER_PATHS = {
    "MainCategory": os.path.join(ENCODER_DIR, "MainCategory_encoder.pkl"),
    "SubCategory": os.path.join(ENCODER_DIR, "SubCategory_encoder.pkl"),
    "Building": os.path.join(ENCODER_DIR, "Building_encoder.pkl"),
    "Site": os.path.join(ENCODER_DIR, "Site_encoder.pkl")
}

def fetch_data_from_mongo():
    print("📡 Fetching data from MongoDB...")
    collection = get_collection(DATABASE_NAME, "service_requests")
    if collection is None:
        print("❌ Failed to connect to collection.")
        return pd.DataFrame()
    data = list(collection.find())
    print(f"✅ Retrieved {len(data)} records.")
    return pd.DataFrame(data)

def target_encode(X, y, columns):
    """Apply target encoding to categorical columns."""
    for col in columns:
        # חישוב ממוצע של היעד (DurationHours) עבור כל קטגוריה
        mean_encoded = X.groupby(col).apply(lambda x: y.loc[x.index].mean())
        # החלפת הערכים בקטגוריות עם הממוצע שנמצא
        X[col] = X[col].map(mean_encoded)
    return X

def preprocess(df):
    print("🧼 Preprocessing data...")

    # המרת התאריך בפורמט מתאים
    df["Created on"] = pd.to_datetime(df["Created on"], errors="coerce")
    df = df.dropna(subset=["Created on"])

    # חישוב זמן טיפול (DurationHours)
    df["DurationHours"] = pd.to_numeric(df["Response time (hours)"], errors="coerce")

    # יצירת תכונות זמן נוספות
    df["Hour"] = df["Created on"].dt.hour
    df["Weekday"] = df["Created on"].dt.weekday  # ימי השבוע (0=ראשון, 1=שני, וכו')
    df["Month"] = df["Created on"].dt.month
    df["DayOfMonth"] = df["Created on"].dt.day
    df["RequestLength"] = df["Request description"].apply(lambda x: len(str(x)))

    # יצירת תכונה של זמן ביום: בוקר, צהריים, ערב
    df['TimeOfDay'] = df['Hour'].apply(lambda x: 'Morning' if 6 <= x < 12 else ('Afternoon' if 12 <= x < 18 else 'Evening'))

    # הוספת שדה IS_WEEKEND - האם היום הוא בסוף שבוע (שבת או יום ראשון)
    df["Is weekend"] = df["Weekday"].isin([5, 6]).astype(int)  # 5=שבת, 6=ראשון
    df["IS_WEEKEND"] = df["Is weekend"]  # תוודא שהשדה יקרא IS_WEEKEND

    # יצירת תכונות אינטראקציה (כמו שעה ויום בשבוע)
    df["Hour_Weekday"] = df["Hour"] * df["Weekday"]

    # יצירת תכונות חדשות עבור ה-preprocessing
    features = [
        "MainCategory", "SubCategory", "Building", "Site",
        "Hour", "Weekday", "Month", "DayOfMonth", "IS_WEEKEND", "RequestLength", "TimeOfDay", "Hour_Weekday"
    ]

    df = df.dropna(subset=features + ["DurationHours"])
    X = df[features].copy()
    y = df["DurationHours"]

    # Target Encoding עבור משתנים קטגוריאליים
    X = target_encode(X, y, columns=["MainCategory", "SubCategory", "Building", "Site", "TimeOfDay"])

    print(f"🧮 Feature matrix shape: {X.shape}, Target shape: {y.shape}")
    return X, y

def train_model():
    print("🚀 Starting training process...")
    df = fetch_data_from_mongo()
    if df.empty:
        print("🛑 No data found. Aborting training.")
        return

    df = shuffle(df, random_state=42).reset_index(drop=True)
    X, y = preprocess(df)

    # Split the data into 80% training and 20% test
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print("🌳 Training LightGBM Regressor model with RandomizedSearchCV and Cross-Validation...")

    # Define parameter grid for LightGBM
    param_dist = {
        'num_leaves': np.arange(20, 150, 10),
        'max_depth': [-1, 5, 10, 20],
        'learning_rate': [0.01, 0.05, 0.1],
        'n_estimators': [100, 200, 300, 500],
        'subsample': [0.7, 0.8, 0.9],
        'min_child_samples': [10, 20, 50],
        'lambda_l1': [0, 0.1, 0.5, 1],
        'lambda_l2': [0, 0.1, 0.5, 1]
    }

    # Initialize LightGBM Regressor
    lgbm = lgb.LGBMRegressor(random_state=42)

    # Initialize RandomizedSearchCV with KFold cross-validation
    random_search = RandomizedSearchCV(lgbm, param_dist, n_iter=10, cv=KFold(n_splits=5, shuffle=True, random_state=42),
                                       n_jobs=-1, verbose=2, random_state=42)

    # Fit the model
    random_search.fit(X_train, y_train)

    # Get the best parameters and model
    best_lgbm_model = random_search.best_estimator_
    print(f"Best parameters found: {random_search.best_params_}")

    # Train the best model
    best_lgbm_model.fit(X_train, y_train)

    print("✅ Model training complete.")
    joblib.dump(best_lgbm_model, MODEL_PATH)
    print(f"💾 Model saved to: {MODEL_PATH}")

if __name__ == "__main__":
    train_model()
   
