# -*- coding: utf-8 -*-
import json
import os
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import mlflow
import pandas as pd
import seaborn as sns
import vertexai
from sklearn.metrics import confusion_matrix, recall_score
from vertexai.preview import generative_models
from vision_ai.base.api import VisionaiAPI
from vision_ai.base.metrics import calculate_metrics, crossentropy
from vision_ai.base.model import Model
from vision_ai.base.pandas import handle_snapshots_df
from vision_ai.base.prompt import get_prompt_api

# Assert all environment variables are set
for var in [
    "MLFLOW_TRACKING_USERNAME",
    "MLFLOW_TRACKING_PASSWORD",
    "VISION_API_USERNAME",
    "VISION_API_PASSWORD",
]:
    assert os.environ.get(var), f"Environment variable {var} is not set"

PROJECT_ID = "rj-vision-ai"
LOCATION = "us-central1"
vertexai.init(project=PROJECT_ID, location=LOCATION)
# from vision_ai.base.prompt import get_prompt_local
# from vision_ai.base.sheets import get_objects_table_from_sheets

ABSOLUTE_PATH = Path(__file__).parent.absolute()
mock_snapshot_data_path = ABSOLUTE_PATH / "mock_snapshots_api_data.json"
mock_final_predicition_path = ABSOLUTE_PATH / "mock_final_predictions.csv"

ARTIFACT_PATH = Path("/tmp/ml_flow_artifacts")
ARTIFACT_PATH.mkdir(exist_ok=True, parents=True)

artifact_input_path = ARTIFACT_PATH / "input.csv"
artifact_output_path = ARTIFACT_PATH / "output.csv"
artifact_output_errors_path = ARTIFACT_PATH / "output_erros.csv"
artifact_input_balance_path = ARTIFACT_PATH / "input_balance.csv"


google_api_model = "gemini-pro-vision"
max_output_tokens = 2048
temperature = 0.2
top_k = 32
top_p = 1


vision_api = VisionaiAPI(
    username=os.environ.get("VISION_API_USERNAME"),
    password=os.environ.get("VISION_API_PASSWORD"),
)


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
# with open(mock_snapshot_data_path, "w") as f:
#     json.dump(snapshots, f)
with open(mock_snapshot_data_path, "r") as f:
    snapshots = json.load(f)


df = pd.DataFrame(snapshots)
df = handle_snapshots_df(df)

# Calculate metrics for each object
df_balance = (
    df[["object", "hard_label", "count"]].groupby(["object", "hard_label"], as_index=False).count()
)
df_balance["percentage"] = round(df_balance["count"] / df_balance["count"].sum(), 2)
df_balance.to_csv(artifact_input_balance_path, index=False)


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
final_predictions = model.predict_batch_mlflow(
    model_input=df, parameters=parameters, max_workers=10
)


final_predictions.to_csv(mock_final_predicition_path, index=False)
# final_predictions = pd.read_csv(mock_final_predicition_path)


# prepare dataframe for mlflow
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

df.to_csv(artifact_input_path, index=False)

final_predictions.to_csv(artifact_output_path, index=False)
final_predictions_errors.to_csv(artifact_output_errors_path, index=False)

# MLFLOW DUMP
mlflow.set_tracking_uri(uri="https://mlflow.dados.rio")

# Create a new MLflow Experiment
tag = "model-evaluation-crossentropy"
today = pd.Timestamp.now().strftime("%Y-%m-%d")
mlflow.set_experiment(f"{today}-{tag}")


# Start an MLflow run
with mlflow.start_run():
    # Log the hyperparameter
    mlflow.log_params(parameters)
    mlflow.log_text(prompt_parameters["prompt_text"], "prompt.md")

    mlflow.log_artifact(artifact_input_path)
    mlflow.log_artifact(artifact_output_path)
    if len(final_predictions_errors) > 0:
        mlflow.log_artifact(artifact_output_errors_path)
    mlflow.log_artifact(artifact_input_balance_path)

    results = {}

    for obj in df["object"].unique():
        df_obj = final_predictions[final_predictions["object"] == obj]
        true_labels = df_obj["label"]
        true_probs = df_obj["distribution"]
        y_true = df_obj["hard_label"].astype(str)
        y_pred = df_obj["label_ia"].astype(str)

        # Choose an appropriate average method (e.g., 'micro', 'macro', or 'weighted')
        average_method = "macro"
        accuracy, precision, recall, f1 = calculate_metrics(y_true, y_pred, average_method)
        crossentropy_loss_mean, crossentropy_loss_std = crossentropy(
            true_labels, true_probs, y_pred
        )
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
        temp_image_path = ARTIFACT_PATH / f"cm_{obj}.png"
        plt.savefig(temp_image_path)

        metrics = {
            f"{obj}_accuracy": accuracy,
            f"{obj}_precision": precision,
            f"{obj}_recall": recall,
            f"{obj}_f1_score": f1,
            f"{obj}_crossentropy_loss": crossentropy_loss_mean,
            f"{obj}_crossentropy_loss_std": crossentropy_loss_std,
        }

        mlflow.log_metric(f"{obj}_crossentropy_loss", crossentropy_loss_mean)
        mlflow.log_metric(f"{obj}_crossentropy_loss_std", crossentropy_loss_std)

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
        mlflow.log_artifact(temp_image_path)

shutil.rmtree(ARTIFACT_PATH)
