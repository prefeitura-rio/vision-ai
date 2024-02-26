# -*- coding: utf-8 -*-
import json

import pandas as pd
from vision_ai.base.shared_models import get_parser


def explode_df(dataframe: pd.DataFrame, column_to_explode: str, prefix: str = None):
    df = dataframe.copy()
    exploded_df = df.explode(column_to_explode)
    new_df = pd.json_normalize(exploded_df[column_to_explode])

    if prefix:
        new_df = new_df.add_prefix(f"{prefix}_")

    df.drop(columns=column_to_explode, inplace=True)
    new_df.index = exploded_df.index
    result_df = df.join(new_df)

    return result_df


def get_objetcs_labels_df(objects: pd.DataFrame, keep_null: bool = True):
    objects_df = objects.rename(columns={"id": "object_id"})
    objects_df = objects_df[["name", "object_id", "labels"]]
    labels = explode_df(objects_df, "labels")
    if not keep_null:
        labels = labels[~labels["value"].isin(["null"])]
    labels = labels.rename(columns={"label_id": "label"})
    labels = labels.reset_index(drop=True)

    mask = (labels["value"] == "null") & (labels["name"] != "image_description")  # noqa
    labels = labels[~mask]

    selected_labels_cols = ["name", "criteria", "identification_guide", "value"]
    labels = labels[selected_labels_cols]
    labels = labels.rename(columns={"name": "object", "value": "label"})
    return labels


def get_prompt_objects_df(labels_df: pd.DataFrame, prompt_objects: list):
    objects_df = pd.DataFrame()
    for obj in prompt_objects:
        obj_labels_df = labels_df[labels_df["object"] == obj]
        obj_labels_df = obj_labels_df.sort_values("label")
        objects_df = pd.concat([objects_df, obj_labels_df])

    return objects_df


def get_prompt(prompt_data: list, objects_data: list):
    prompt_parameters = prompt_data[0]
    prompt_text = prompt_parameters.get("prompt_text")
    prompt_objects = prompt_parameters.get("objects")

    labels_df = get_objetcs_labels_df(objects=pd.DataFrame(objects_data), keep_null=True)
    objects_table = get_prompt_objects_df(
        labels_df=labels_df,
        prompt_objects=prompt_objects,
    )
    objects_table_md = objects_table.to_markdown(index=False)

    _, output_schema_parsed, output_example_parsed = get_parser()
    output_schema = json.dumps(json.loads(output_schema_parsed), indent=4)
    output_example = json.dumps(json.loads(output_example_parsed), indent=4)

    prompt_text = (
        prompt_text.replace("{objects_table_md}", objects_table_md)
        .replace("{output_schema}", output_schema)
        .replace("{output_example}", output_example)
    )

    return prompt_text, objects_table
