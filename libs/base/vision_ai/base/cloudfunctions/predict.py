# -*- coding: utf-8 -*-
import traceback

from langchain.output_parsers import PydanticOutputParser
from vision_ai.base.cloudfunctions.bq import save_data_in_bq
from vision_ai.base.model import Model
from vision_ai.base.shared_models import Output


def get_prediction(
    bq_data_json: dict,
    image_url: str,
    prompt_text: str,
    google_api_model: str,
    max_output_tokens: int,
    temperature: float,
    top_k: int,
    top_p: int,
    safety_settings: dict,
    project_id: str,
    dataset_id: str,
    table_id: str,
):
    try:
        model = Model()
        responses = model.llm_vertexai(
            image_url=image_url,
            prompt_text=prompt_text,
            google_api_model=google_api_model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            safety_settings=safety_settings,
        )
        ai_response = responses.text

    except Exception as exception:
        save_data_in_bq(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            json_data=bq_data_json,
            error_step="ai_request",
            ai_response_parsed=None,
            ai_response=None,
            error_message=str(traceback.format_exc(chain=False)),
            error_name=str(type(exception).__name__),
        )
        raise exception

    output_parser = PydanticOutputParser(pydantic_object=Output)

    try:
        response_parsed = output_parser.parse(ai_response)
    except Exception as exception:
        save_data_in_bq(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            json_data=bq_data_json,
            error_step="ai_response_parser",
            ai_response_parsed=None,
            ai_response=ai_response,
            error_message=str(traceback.format_exc(chain=False)),
            error_name=str(type(exception).__name__),
        )
        raise exception

    return response_parsed.dict()
