# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import mlflow
import pandas as pd
import seaborn as sns
import vertexai
from sklearn.metrics import confusion_matrix, recall_score
from vertexai.preview import generative_models
from vision_ai.base.api import VisionaiAPI
from vision_ai.base.metrics import calculate_metrics
from vision_ai.base.model import Model
from vision_ai.base.pandas import explode_df
from vision_ai.base.prompt import get_prompt_api

# from vision_ai.base.prompt import get_prompt_local
# from vision_ai.base.sheets import get_objects_table_from_sheets

ABSOLUTE_PATH = Path(__file__).parent.absolute()

# os.environ["MLFLOW_TRACKING_USERNAME"] = secret["mlflow_tracking_username"]
# os.environ["MLFLOW_TRACKING_PASSWORD"] = secret["mlflow_tracking_password"]

vision_api = VisionaiAPI(
    username=os.environ.get("VISION_API_USERNAME"),
    password=os.environ.get("VISION_API_PASSWORD"),
)


PROJECT_ID = "rj-escritorio-dev"
LOCATION = "us-central1"
VERSION_ID = "latest"
DATASET_ID = "vision_ai"
TABLE_ID = "cameras_predicoes"
vertexai.init(project=PROJECT_ID, location=LOCATION)

SAFETY_CONFIG = {
    generative_models.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: generative_models.HarmBlockThreshold.BLOCK_NONE,
    generative_models.HarmCategory.HARM_CATEGORY_HARASSMENT: generative_models.HarmBlockThreshold.BLOCK_NONE,
    generative_models.HarmCategory.HARM_CATEGORY_HATE_SPEECH: generative_models.HarmBlockThreshold.BLOCK_NONE,
    generative_models.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: generative_models.HarmBlockThreshold.BLOCK_NONE,
}


# LOCAL PROMPT + OBJECTS TABLE FROM SHEETS
# with open("./projects/mlflow/prompt.md") as f:
#     prompt_text_local = f.read()
# objects_table_md, objects_labels_md = get_objects_table_from_sheets()
# prompt, prompt_template = get_prompt_local(
#     prompt_parameters=None, prompt_template=prompt_text_local, objects_table_md=objects_table_md
# )

# GET PROMPT FROM API
prompt_data = vision_api._get_all_pages(path="/prompts")
objects_data = vision_api._get_all_pages(path="/objects")
prompt_parameters, _ = get_prompt_api(
    prompt_name="base", prompt_data=prompt_data, objects_data=objects_data
)

# GET SNAPSHOTS. API OR MOCK
# snapshots = vision_api._get(path="/identifications/aggregate")
# with open(ABSOLUTE_PATH / "mock_snapshots_api_data.json", "w") as f:
#     json.dump(snapshots, f)

with open(ABSOLUTE_PATH / "mock_snapshots_api_data.json", "r") as f:
    snapshots = json.load(f)


df = pd.DataFrame(snapshots)
df = explode_df(df, "human_identification")
df = df.drop(columns=["ia_identification"])
df = df.sort_values(by=["snapshot_id", "object", "count"], ascending=False)
df = df.drop_duplicates(subset=["snapshot_id", "object"], keep="first")


google_api_model = "gemini-pro-vision"
max_output_tokens = 2048
temperature = 0.2
top_k = 32
top_p = 1


model = Model()
parameters = {
    "prompt": prompt_parameters["prompt_text"],
    "google_api_model": google_api_model,
    "temperature": temperature,
    "top_k": top_k,
    "top_p": top_p,
    "max_output_tokens": max_output_tokens,
    "safety_settings": SAFETY_CONFIG,
}


# START PREDICTIONS
# final_predictions = model.predict_batch_mlflow(
#     model_input=df, parameters=parameters, max_workers=10
# )
# final_predictions.to_csv(ABSOLUTE_PATH / "mock_final_predictions.csv", index=False)
final_predictions = pd.read_csv(ABSOLUTE_PATH / "mock_final_predictions.csv")


# prepare dataframe for mlflow
final_predictions["label"] = final_predictions["label"].fillna("null")
final_predictions["label_ia"] = final_predictions["label_ia"].fillna("null")
final_predictions["label_ia"] = final_predictions["label_ia"].apply(lambda x: str(x).lower())


parameters.pop("prompt")
parameters["safety_settings"] = json.dumps(SAFETY_CONFIG, indent=4)
parameters["number_images"] = len(final_predictions["snapshot_id"].unique())

mask = (final_predictions["object"] == "image_corrupted") & (
    final_predictions["label_ia"] == "prediction_error"
)

final_predictions_errors = final_predictions[mask]
final_predictions = final_predictions[~mask]

parameters["number_errors"] = len(final_predictions_errors["snapshot_id"].unique())

# MLFLOW DUMP
mlflow.set_tracking_uri(uri="https://mlflow.dados.rio")

# Create a new MLflow Experiment
tag = "model-evaluation"
today = pd.Timestamp.now().strftime("%Y-%m-%d")
mlflow.set_experiment(f"{today}-{tag}")


# Start an MLflow run
with mlflow.start_run():
    # Log the hyperparameter
    mlflow.log_params(parameters)
    mlflow.log_text(prompt_parameters["prompt_text"], "prompt.md")
    mlflow.log_input(mlflow.data.from_pandas(df), context="input")
    mlflow.log_input(mlflow.data.from_pandas(final_predictions), context="output")
    if len(final_predictions_errors) > 0:
        mlflow.log_input(mlflow.data.from_pandas(final_predictions_errors), context="output_errors")

    # Calculate metrics for each object
    results = {}
    for obj in final_predictions["object"].unique():
        df_obj = final_predictions[final_predictions["object"] == obj]
        y_true = df_obj["label"]
        y_pred = df_obj["label_ia"]

        # Choose an appropriate average method (e.g., 'micro', 'macro', or 'weighted')
        average_method = "macro"
        accuracy, precision, recall, f1 = calculate_metrics(y_true, y_pred, average_method)
        unique_labels = sorted(set(y_true) | set(y_pred))
        cm = confusion_matrix(y_true, y_pred, labels=unique_labels)

        plt.figure(figsize=(8, 6))
        sns.heatmap(
            cm,
            annot=True,
            fmt="d",
            cmap="Blues",
            xticklabels=unique_labels,
            yticklabels=unique_labels,
        )
        plt.ylabel("Actual")
        plt.xlabel("Predicted")
        plt.title(f"Confusion Matrix for {obj}")
        # Save image temporarily
        temp_image_path = f"/tmp/cm_{obj}.png"
        plt.savefig(temp_image_path)

        metrics = {
            f"{obj}_accuracy": accuracy,
            f"{obj}_precision": precision,
            f"{obj}_recall": recall,
            f"{obj}_f1_score": f1,
        }

        if obj == "image_corrupted":
            mlflow.log_metric(f"{obj}_recall", recall)
            mlflow.log_metric(f"{obj}_precision", precision)
        elif obj == "rain":
            mlflow.log_metric(f"{obj}_f1_score", f1)
        elif obj in ["water_level", "road_blockade"]:
            mlflow.log_metric(f"{obj}_recall", recall)
            recall_per_label = recall_score(
                y_true, y_pred, average=None, labels=unique_labels, zero_division=0
            )
            for i, label in enumerate(unique_labels):
                if label not in ["null", "free", "low"]:
                    mlflow.log_metric(f"{obj}_{label}_recall", recall_per_label[i])
        # mlflow.log_metrics(metrics)
        mlflow.log_artifact(f"/tmp/cm_{obj}.png")
