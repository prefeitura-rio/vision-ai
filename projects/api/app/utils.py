# -*- coding: utf-8 -*-
import asyncio
import base64
import inspect
import io
import json
from asyncio import Task
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

import nest_asyncio
from fastapi import HTTPException, status
from google.cloud import pubsub, storage
from google.cloud.storage.blob import Blob
from google.oauth2 import service_account
from PIL import Image
from pydantic import BaseModel
from tortoise.models import Model
from vision_ai.base.shared_models import Output, OutputFactory

from app import config
from app.models import Label, Object, Prompt


def _to_task(future, as_task, loop):
    if not as_task or isinstance(future, Task):
        return future
    return loop.create_task(future)


def apply_to_list(lst: list[Any], fn: Callable) -> list[Any]:
    """
    Applies a function to a whole list and returns the result.
    """
    return [fn(item) for item in lst]


def asyncio_run(future, as_task=True):
    """
    A better implementation of `asyncio.run`.

    :param future: A future or task or call of an async method.
    :param as_task: Forces the future to be scheduled as task (needed for e.g. aiohttp).
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no event loop running:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(_to_task(future, as_task, loop))
    else:
        nest_asyncio.apply(loop)
        return asyncio.run(_to_task(future, as_task, loop))


def download_camera_snapshot_from_bucket(*, camera_id: str) -> str:
    """
    Downloads a camera snapshot from the bucket.
    Mode needs to be "prod" or "staging"

    Args:
        camera_id (str): The camera id.

    Returns:
        str: The base64 encoded image.
    """
    # Set blob path
    blob_path = generate_blob_path(camera_id)
    # Download file
    tmp_fname = f"/tmp/{uuid4()}.png"
    download_file_from_bucket(
        bucket_name=config.GCS_BUCKET_NAME,
        source_blob_name=blob_path,
        destination_file_name=tmp_fname,
    )
    # Read file
    with open(tmp_fname, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")
    return image_base64


def download_file_from_bucket(
    bucket_name: str, source_blob_name: str, destination_file_name: str
) -> None:
    """
    Downloads a file from the bucket.
    Mode needs to be "prod" or "staging"

    Args:
        bucket_name (str): The name of the bucket.
        source_blob_name (str): The name of the blob.
        destination_file_name (str): The path of the file to download to.

    Returns:
        None
    """
    storage_client = get_gcs_client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    blob.download_to_filename(destination_file_name)


def fn_is_async(fn: Callable) -> bool:
    """
    Checks if a function is async.

    Args:
        fn (Callable): The function to check.

    Returns:
        bool: True if the function is async, False otherwise.
    """
    return inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)


def generate_blob_path(camera_id: str, snapshot_id: str = str(uuid4)) -> str:
    """
    Generates a blob path for a camera snapshot.

    Args:
        camera_id (str): The camera id.

    Returns:
        str: The blob path.
    """
    path_data = datetime.now().strftime("ano=%Y/mes=%m/dia=%d")
    return f"{config.GCS_BUCKET_PATH_PREFIX}/{path_data}/camera_id={camera_id}/{snapshot_id}.png"


def get_camera_snapshot_blob_url(*, camera_id: str) -> str:
    """
    Gets the blob URL for a camera snapshot.

    Args:
        camera_id (str): The camera id.

    Returns:
        str: The blob URL.
    """
    blob_path = generate_blob_path(camera_id)
    bucket_name = config.GCS_BUCKET_NAME
    storage_client = get_gcs_client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    return blob.public_url


def get_gcp_credentials(
    scopes: list[str] = None,
) -> service_account.Credentials:
    """
    Gets credentials from env vars
    """
    env: str = config.GCP_SERVICE_ACCOUNT_CREDENTIALS
    if not env:
        raise ValueError("GCP_SERVICE_ACCOUNT_CREDENTIALS env var not set!")
    info: dict = json.loads(base64.b64decode(env))
    cred: service_account.Credentials = service_account.Credentials.from_service_account_info(info)
    if scopes:
        cred = cred.with_scopes(scopes)
    return cred


def get_gcs_client() -> storage.Client:
    """
    Get a GCS client with the credentials from the environment.

    Args:
        mode (str): The mode to filter by (prod or staging).

    Returns:
        storage.Client: The GCS client.
    """
    credentials = get_gcp_credentials()
    return storage.Client(credentials=credentials)


async def get_objects_table(objects: list[Object]) -> str:
    """
    Fetches all `Label` objects for the provided `objects` and return
    a markdown table with the following format:

    | object | criteria | identification_guide | label |
    | ------ | -------- | -------------------- | ----- |
    | ...    | ...      | ...                  | ...   |

    Args:
        objects (list[Object]): The objects to fetch.

    Returns:
        str: The markdown table.
    """
    # Fetch all labels for the objects
    labels = set()
    for object_ in objects:
        labels.update(await Label.filter(object__id=object_.id).all())

    # Create the header
    header = "| object | criteria | identification_guide | label |\n"
    header += "| ------ | -------- | -------------------- | ----- |\n"

    # Create the rows
    rows = ""
    for label in labels:
        rows += f"| {(await label.object).slug} | {label.criteria} | {label.identification_guide} | {label.value} |\n"  # noqa

    # Return the table
    return header + rows


def get_output_schema_and_sample() -> tuple[str, str]:
    """
    Gets the output schema and sample for the vision AI model.

    Returns:
        tuple[str, str]: The output schema and sample.
    """
    output_schema = Output.schema_json(indent=4)
    output_sample = json.dumps(OutputFactory.generate_sample().dict(), indent=4)
    return output_schema, output_sample


async def get_prompt_formatted_text(prompt: Prompt, object_slugs: list[str]) -> str:
    """
    Gets the full text of a prompt.

    Args:
        prompt (Prompt): The prompt.

    Returns:
        str: The full text of the prompt.
    """
    # Filter object slugs that are in the prompt objects
    object_slugs = [
        slug
        for slug in object_slugs
        if slug in [object_.slug for object_ in await prompt.objects.all()]
    ]
    objects = await Object.filter(slug__in=object_slugs).all()
    objects_table_md = await get_objects_table(objects)
    output_schema, output_example = get_output_schema_and_sample()
    template = prompt.prompt_text
    template = template.format(
        objects_table_md=objects_table_md,
        output_schema=output_schema,
        output_example=output_example,
    )
    return template


async def get_prompts_best_fit(object_slugs: list[str]) -> list[Prompt]:
    """
    Gets the best fit prompts for a list of objects.

    Args:
        object_slugs (list[str]): The slugs for the objects.

    Returns:
        list[Prompt]: The best fit prompts.
    """
    prompts: list[Prompt] = []
    objects: list[Object] = []
    for object_slug in object_slugs:
        object_ = await Object.get_or_none(slug=object_slug)
        if object_ is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")
        objects.append(object_)
        prompts += list(await Prompt.filter(objects__id=object_.id).all())
    # Rank prompts by number of objects in common
    prompt_scores = {}
    for prompt in prompts:
        for object_ in objects:
            if object_ in await prompt.objects.all():
                prompt_scores[prompt.id] = prompt_scores.get(prompt.id, 0) + 1
    # Sort prompts by score
    prompt_scores = sorted(prompt_scores.items(), key=lambda x: x[1], reverse=True)
    # Start a final list of prompts
    final_prompts: list[Prompt] = []
    covered_objects: list[Object] = []
    # For each prompt, add it to the final list if its objects are not already covered
    for prompt_id, _ in prompt_scores:
        prompt = await Prompt.get_or_none(id=prompt_id)
        if prompt is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
        if not set(await prompt.objects.all()).intersection(set(covered_objects)):
            final_prompts.append(prompt)
            covered_objects += list(await prompt.objects.all())
    return final_prompts


def get_pubsub_client() -> pubsub.PublisherClient:
    """
    Get a PubSub client with the credentials from the environment.

    Args:
        mode (str): The mode to filter by (prod or staging).

    Returns:
        storage.Client: The GCS client.
    """
    credentials = get_gcp_credentials(scopes=["https://www.googleapis.com/auth/pubsub"])
    return pubsub.PublisherClient(credentials=credentials)


def publish_message(
    *,
    data: dict[str, str],
    project_id: str = config.GCP_PUBSUB_PROJECT_ID,
    topic: str = config.GCP_PUBSUB_TOPIC_NAME,
):
    """
    Publishes a message to a PubSub topic.

    Args:
        data (dict[str, str]): The data to publish.
        project_id (str): The project id.
        topic (str): The topic name.
    """
    client = get_pubsub_client()
    topic_name = f"projects/{project_id}/topics/{topic}"
    byte_data = json.dumps(data, default=str).encode("utf-8")
    future = client.publish(topic_name, byte_data)
    return future.result()


def slugify(text: str) -> str:
    """
    Slugifies a string.

    Args:
        text (str): The string to slugify.

    Returns:
        str: The slugified string.
    """
    return text.lower().replace(" ", "-").replace("_", "-").replace(".", "-").strip()


def transform_tortoise_to_pydantic(
    tortoise_model: Model,
    pydantic_model: BaseModel,
    vars_map: list[tuple[str, str | tuple[str, Callable]]],
) -> BaseModel:
    """
    Transform a Tortoise ORM model to a Pydantic model using a variable mapping.

    Args:
        tortoise_model (Model): The Tortoise ORM model to transform.
        pydantic_model (BaseModel): The Pydantic model to transform into.
        vars_map (dict[str, str]): A dictionary mapping Tortoise ORM variable names
                                   to Pydantic variable names.

    Returns:
        BaseModel: An instance of the Pydantic model with values from the Tortoise model.
    """
    # Create a dictionary to store Pydantic field values
    pydantic_values = {}

    # Iterate through the variable mapping
    for tortoise_var, pydantic_var in vars_map:
        # Get the value from the Tortoise model
        tortoise_value = getattr(tortoise_model, tortoise_var, None)

        # If pydanctic_var is a tuple, it means that we need to apply a function to the value
        if isinstance(pydantic_var, tuple):
            pydantic_var, fn = pydantic_var
            if fn_is_async(fn):
                tortoise_value = asyncio_run(fn(tortoise_value))
            else:
                tortoise_value = fn(tortoise_value)

        # Set the value in the Pydantic dictionary
        pydantic_values[pydantic_var] = tortoise_value

    # Create an instance of the Pydantic model with the transformed values
    transformed_pydantic_model = pydantic_model(**pydantic_values)

    return transformed_pydantic_model


def upload_camera_snapshot_to_bucket(*, image_base64: str, camera_id: str) -> str:
    """
    Uploads a camera snapshot to the bucket.
    Mode needs to be "prod" or "staging"

    Args:
        image_base64 (str): The base64 encoded image.
        camera_id (str): The camera id.

    Returns:
        str: The public URL of the uploaded image.
    """
    # Save image to temp file
    tmp_fname = f"/tmp/{uuid4()}.png"
    img = Image.open(io.BytesIO(base64.b64decode(image_base64)))
    with open(tmp_fname, "wb") as f:
        img.save(f, format="PNG")
    # Set blob path
    blob_path = generate_blob_path(camera_id)
    blob = upload_file_to_bucket(
        bucket_name=config.GCS_BUCKET_NAME,
        file_path=tmp_fname,
        destination_blob_name=blob_path,
    )
    # Return public URL
    return blob.public_url


def upload_file_to_bucket(bucket_name: str, file_path: str, destination_blob_name: str) -> "Blob":
    """
    Uploads a file to the bucket.
    Mode needs to be "prod" or "staging"

    Args:
        bucket_name (str): The name of the bucket.
        file_path (str): The path of the file to upload.
        destination_blob_name (str): The name of the blob.

    Returns:
        Blob: The uploaded blob.
    """
    storage_client = get_gcs_client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(file_path)
    return blob
