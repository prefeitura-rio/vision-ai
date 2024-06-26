# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

EPSILON = np.finfo(np.float32).eps


# Define a function to handle null values
def calculate_metrics(y_true, y_pred, average):
    # ... (same as before) ...
    # Filter out null values
    valid_indices = y_true.notnull() & y_pred.notnull()
    y_true_filtered = y_true[valid_indices]
    y_pred_filtered = y_pred[valid_indices]

    accuracy = accuracy_score(y_true_filtered, y_pred_filtered)
    precision = precision_score(y_true_filtered, y_pred_filtered, average=average, zero_division=0)
    recall = recall_score(y_true_filtered, y_pred_filtered, average=average, zero_division=0)
    f1 = f1_score(y_true_filtered, y_pred_filtered, average=average, zero_division=0)

    return accuracy, precision, recall, f1


def water_level_custom_metric(y_true, y_pred):
    # Assuming y_true and y_pred are Pandas Series with categories like 'low', 'medium', 'high'
    true_high = y_true == "high"
    true_medium = y_true == "medium"
    pred_low = y_pred == "low"

    # Calculate M_high
    M_high = 1 - ((pred_low & true_high).sum() / true_high.sum() if true_high.sum() > 0 else 0)

    # Calculate M_medium
    M_medium = 1 - (
        (pred_low & true_medium).sum() / true_medium.sum() if true_medium.sum() > 0 else 0
    )

    return M_high, M_medium


def crossentropy(
    true_labels_series: pd.Series,
    true_probs_series: pd.Series,
    y_pred_series: pd.Series,
) -> float:
    """
    Calculate the cross-entropy loss between true and predicted label.
    """
    crossentropies = []
    for true_labels, true_probs, y_pred in zip(
        true_labels_series, true_probs_series, y_pred_series
    ):
        if isinstance(true_labels, str):
            true_labels = eval(true_labels)
        if isinstance(true_probs, str):
            true_probs = eval(true_probs)
        true_labels = [str(label) for label in true_labels]
        true_probs = [float(prob) for prob in true_probs]
        if str(y_pred) not in true_labels:
            true_labels.append(str(y_pred))
            true_probs.append(0)
        true_probs = np.array(true_probs)
        pred_probs = np.zeros(len(true_probs))
        pred_probs[true_labels.index(str(y_pred))] = 1
        # Switch variables
        a = true_probs
        true_probs = pred_probs
        pred_probs = a
        crossentropies.append(
            -np.sum(
                true_probs * np.log(pred_probs + EPSILON)
                + (1 - true_probs) * np.log(1 - pred_probs + EPSILON)
            )
        )
    return np.mean(crossentropies), np.std(crossentropies)
