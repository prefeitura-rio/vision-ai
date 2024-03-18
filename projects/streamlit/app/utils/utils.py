# -*- coding: utf-8 -*-
import json  # noqa
from pathlib import Path
from typing import Union

import folium
import pandas as pd
import streamlit as st
from st_aggrid import GridOptionsBuilder  # noqa
from st_aggrid import GridUpdateMode  # noqa
from st_aggrid import AgGrid, ColumnsAutoSizeMode
from vision_ai.base.api import VisionaiAPI
from vision_ai.base.pandas import explode_df

STREAMLIT_PATH = Path(__file__).parent.parent.parent.absolute()
CACHE_MINUTES = 5


def get_vision_ai_api():
    def user_is_logged_in():
        if "logged_in" not in st.session_state:
            st.session_state["logged_in"] = False

        def callback_data():
            username = st.session_state["username"]
            password = st.session_state["password"]
            try:
                _ = VisionaiAPI(
                    username=username,
                    password=password,
                )
                st.session_state["logged_in"] = True
            except Exception as exc:
                st.error(f"Error: {exc}")
                st.session_state["logged_in"] = False

        if st.session_state["logged_in"]:
            return True

        st.write("Please login")
        st.text_input("Username", key="username")
        st.text_input("Password", key="password", type="password")
        st.button("Login", on_click=callback_data)
        return False

    if not user_is_logged_in():
        st.stop()

    vision_api = VisionaiAPI(
        username=st.session_state["username"],
        password=st.session_state["password"],
    )
    return vision_api


vision_api = get_vision_ai_api()

# import os

# vision_api = VisionaiAPI(
#     username=os.environ.get("VISION_API_USERNAME"),
#     password=os.environ.get("VISION_API_PASSWORD"),
# )


def get_cameras(
    only_active=True,
    use_mock_data=False,
    update_mock_data=False,
    page_size=3000,
    timeout=120,
):
    mock_data_path = STREAMLIT_PATH / "data/temp/mock_api_data.json"

    if use_mock_data:
        with open(mock_data_path) as f:
            data = json.load(f)
        return data
    if only_active:
        cameras_ativas = vision_api._get_all_pages(
            "/agents/89173394-ee85-4613-8d2b-b0f860c26b0f/cameras"
        )
        cameras_ativas_ids = [f"/cameras/{d.get('id')}" for d in cameras_ativas]  # noqa
        data = vision_api._get_all_pages(cameras_ativas_ids, timeout=timeout)
    else:
        data = vision_api._get_all_pages(path="/cameras", page_size=page_size, timeout=timeout)

    if update_mock_data:
        with open(mock_data_path, "w") as f:
            json.dump(data, f)

    return data


def get_objects(
    page_size=100,
    timeout=120,
):
    data = vision_api._get_all_pages(path="/objects", page_size=page_size, timeout=timeout)
    return data


def get_prompts(
    page_size=100,
    timeout=120,
):
    data = vision_api._get_all_pages(path="/prompts", page_size=page_size, timeout=timeout)
    return data


def get_ai_identifications(
    page_size=100,
    timeout=120,
):
    data = vision_api._get_all_pages(
        path="/identifications/ai", page_size=page_size, timeout=timeout
    )
    return data


def get_hide_identifications():
    return vision_api._get(path="/identifications/hide")


def send_user_identification(identification_id, label):
    json = {"identification_id": identification_id, "label": label}
    vision_api._post(path="/identifications", json_data=json)


def send_hide_identification(identifications_id):
    json = {"identifications_id": identifications_id}
    vision_api._post(path="/identifications/hide", json_data=json)


@st.cache_data(ttl=60 * CACHE_MINUTES, persist=False)
def get_cameras_cache(
    only_active=True,
    use_mock_data=False,
    update_mock_data=False,
    page_size=3000,
    timeout=120,
):
    return get_cameras(
        only_active=only_active,
        use_mock_data=use_mock_data,
        update_mock_data=update_mock_data,
        page_size=page_size,
        timeout=timeout,
    )


@st.cache_data(ttl=60 * CACHE_MINUTES, persist=False)
def get_objects_cache(page_size=100, timeout=120):
    return get_objects(page_size=page_size, timeout=timeout)


@st.cache_data(ttl=60 * CACHE_MINUTES, persist=False)
def get_prompts_cache(page_size=100, timeout=120):
    return get_prompts(page_size=page_size, timeout=timeout)


@st.cache_data(ttl=60 * 30, persist=False)
def get_ai_identifications_cache(page_size=3000, timeout=120):
    return get_ai_identifications(page_size=page_size, timeout=timeout)


def treat_data(response, hides):
    cameras_aux = pd.read_csv(STREAMLIT_PATH / "data/database/cameras_aux.csv", dtype=str)

    cameras_aux = cameras_aux.rename(columns={"id_camera": "camera_id"})
    cameras = pd.DataFrame(response)
    cameras = cameras.rename(columns={"id": "camera_id"})
    cameras = cameras[cameras["identifications"].apply(lambda x: len(x) > 0)]
    if len(cameras) == 0:
        return None, None
    cameras = cameras.merge(cameras_aux, on="camera_id", how="left")
    # st.dataframe(cameras)

    cameras_attr = cameras[
        [
            "camera_id",
            "bairro",
            "subprefeitura",
            "name",
            # "rtsp_url",
            # "update_interval",
            "latitude",
            "longitude",
            "identifications",
            # "snapshot_url",
            # "id_h3",
            # "id_bolsao",
            # "bolsao_latitude",
            # "bolsao_longitude",
            # "bolsao_classe_atual",
            # "bacia",
            # "sub_bacia",
            # "geometry_bolsao_buffer_0.002",
        ]
    ]

    cameras_identifications_explode = explode_df(cameras_attr, "identifications")  # noqa

    cameras_identifications_explode = cameras_identifications_explode.rename(
        columns={"id": "identification_id"}
    ).rename(columns={"camera_id": "id"})
    cameras_identifications_explode = cameras_identifications_explode.rename(
        columns={
            "snapshot.id": "snapshot_id",
            "snapshot.camera_id": "snapshot_camera_id",
            "snapshot.image_url": "snapshot_url",
            "snapshot.timestamp": "snapshot_timestamp",
        }
    )

    cameras_identifications_explode["timestamp"] = pd.to_datetime(
        cameras_identifications_explode["timestamp"], format="ISO8601"
    ).dt.tz_convert("America/Sao_Paulo")

    cameras_identifications_explode["snapshot_timestamp"] = pd.to_datetime(
        cameras_identifications_explode["snapshot_timestamp"], format="ISO8601"
    ).dt.tz_convert("America/Sao_Paulo")

    cameras_identifications_explode = cameras_identifications_explode.sort_values(  # noqa
        ["timestamp", "label"], ascending=False
    )

    # select image_corrupted is true
    mask = (cameras_identifications_explode["object"] == "image_corrupted") & (
        cameras_identifications_explode["label"] == "true"
    )  # noqa
    # select all rows with same snapshot_id of the mask except the rows with object = image_corrupted
    cameras_identifications_explode = cameras_identifications_explode[
        ~(
            (
                cameras_identifications_explode["snapshot_id"].isin(
                    cameras_identifications_explode[mask]["snapshot_id"]
                )
                & (cameras_identifications_explode["object"] != "image_corrupted")
            )
        )
    ]

    cameras_identifications_descriptions = cameras_identifications_explode[
        cameras_identifications_explode["object"] == "image_description"
    ]
    # remove "image_description" from the objects
    cameras_identifications_explode = cameras_identifications_explode[
        cameras_identifications_explode["object"] != "image_description"
    ]
    # remove "null" from the labels
    cameras_identifications_explode = cameras_identifications_explode[
        cameras_identifications_explode["label"] != "null"
    ]

    # # create a column order to sort the labels
    cameras_identifications_explode = create_order_column(cameras_identifications_explode)
    # remove hide identifications
    hide_ids = [hide["snapshot"]["camera_id"] for hide in hides]
    cameras_identifications_explode = cameras_identifications_explode[
        ~cameras_identifications_explode["id"].isin(hide_ids)
    ]
    cameras_identifications_explode = cameras_identifications_explode.sort_values(
        ["object", "order"]
    )

    # # print one random row of the dataframe in list format so I can see all the columns
    # print(cameras_identifications_explode.sample(1).values.tolist())

    # # print all columns of cameras_identifications_explode
    # print(cameras_identifications_explode.columns)
    return cameras_identifications_explode, pd.concat(
        [cameras_identifications_explode, cameras_identifications_descriptions]
    )


def get_filted_cameras_objects(cameras_identifications_df, object_filter, label_filter):  # noqa
    # filter both dfs by object and label

    cameras_identifications_filter_df = cameras_identifications_df[
        (cameras_identifications_df["title"] == object_filter)
        & (cameras_identifications_df["label_text"].isin(label_filter))
    ]

    cameras_identifications_filter_df = cameras_identifications_filter_df.sort_values(  # noqa
        by=["timestamp", "label"], ascending=False
    )

    return cameras_identifications_filter_df


def get_icon_color(label: Union[bool, None], type=None):
    red = [
        "major",
        "totally_blocked",
        "impossible",
        "impossibe",
        "poor",
        "true",
        "flodding",
        "high",
        "totally",
    ]
    orange = [
        "minor",
        "partially_blocked",
        "difficult",
        "puddle",
        "medium",
        "moderate",
        "partially",
    ]

    green = [
        "normal",
        "free",
        "easy",
        "clean",
        "false",
        "low_indifferent",
        "low",
    ]
    if label in red:  # noqa
        if type == "emoji":
            return "🔴"
        return "red"

    elif label in orange:
        if type == "emoji":
            return "🟠"
        return "orange"
    elif label in green:
        if type == "emoji":
            return "🟢"
        return "green"
    else:
        if type == "emoji":
            return "⚫"
        return "grey"


def create_map(chart_data, location=None):
    chart_data = chart_data.fillna("")
    # center map on the mean of the coordinates
    if location is not None:
        m = folium.Map(location=location, zoom_start=16)
    elif len(chart_data) > 0:
        m = folium.Map(
            location=[
                chart_data["latitude"].mean(),
                chart_data["longitude"].mean(),
            ],  # noqa
            zoom_start=11,
        )
    else:
        m = folium.Map(location=[-22.917690, -43.413861], zoom_start=11)

    for _, row in chart_data.iterrows():
        icon_color = get_icon_color(row["label"])
        htmlcode = f"""<div>
        <img src="{row["snapshot_url"]}" width="300" height="185">

        <br /><span>ID: {row["id"]}<br>Label: {row["label_text"]}</span>
        </div>
        """
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            # Adicionar id_camera ao tooltip
            tooltip=f"ID: {row['id']}<br>Label: {row['label_text']}",
            # Alterar a cor do ícone de acordo com o status
            popup=htmlcode,
            icon=folium.features.DivIcon(
                icon_size=(15, 15),
                icon_anchor=(7, 7),
                html=f'<div style="width: 15px; height: 15px; background-color: {icon_color}; border: 2px solid black; border-radius: 70%;"></div>',  # noqa
            ),
        ).add_to(m)
    return m


def display_identification(identification, siblings):
    with st.container(border=True):
        st.markdown(f'**{identification["title"]}**')
        if identification["label"] != "null":
            st.markdown(
                f'**:{get_icon_color(identification["label"])}[{identification["label_text"]}]**'
            )
        st.markdown(f'**Descrição:** {identification["label_explanation"]}')
        if st.button("Identificação errada", key=identification["identification_id"]):
            identifications_id = [identification["identification_id"]]
            if identification["object"] == "image_corrupted":
                identifications_id += [sibling["identification_id"] for sibling in siblings]
            send_hide_identification(identifications_id)
            st.rerun()


def display_camera_details(row, cameras_identifications_df):
    camera_id = row["id"]
    image_url = row["snapshot_url"]
    camera_name = row["name"]
    snapshot_timestamp = row["snapshot_timestamp"].strftime("%d/%m/%Y %H:%M")  # noqa

    st.markdown(f"### 📷 Camera snapshot")  # noqa
    st.markdown(f"Endereço: {camera_name}")
    # st.markdown(f"Data Snapshot: {snapshot_timestamp}")

    # get cameras_attr url from selected row by id
    if image_url is None:
        st.markdown("Falha ao capturar o snapshot da câmera.")
    else:
        st.image(image_url, use_column_width=True)

    st.markdown("### 📃 Detalhes")
    camera_identifications = cameras_identifications_df[
        cameras_identifications_df["id"] == camera_id
    ]  # noqa

    # st.dataframe(camera_identifications)

    camera_identifications = camera_identifications.reset_index(drop=True)

    camera_identifications[""] = camera_identifications["label"].apply(
        lambda x: get_icon_color(x, type="emoji")
    )
    camera_identifications.index = camera_identifications[""]
    camera_identifications = camera_identifications[camera_identifications["timestamp"].notnull()]

    st.markdown(f"**Data Captura:** {snapshot_timestamp}")
    items = []
    for _, row in camera_identifications.iterrows():
        items.append(row)

    for i in range(1, len(items), 2):
        col1, col2 = st.columns(2)
        with col1:
            display_identification(items[i - 1], siblings=items)
        with col2:
            display_identification(items[i], siblings=items)

    if len(items) % 2 == 1:
        display_identification(items[len(items) - 1], siblings=items)


def display_agrid_table(table):
    gb = GridOptionsBuilder.from_dataframe(table, index=True)  # noqa

    gb.configure_column("index", header_name="", pinned="left")
    gb.configure_column("title", header_name="Identificador", wrapText=True)
    gb.configure_column("label_text", header_name="Classificação", wrapText=True)
    gb.configure_column("bairro", header_name="Bairro", wrapText=True)
    gb.configure_column("id", header_name="ID Camera", pinned="right")  # noqa
    gb.configure_column("timestamp", header_name="Data Identificação", wrapText=True)  # noqa
    # gb.configure_column(
    #     "snapshot_timestamp",
    #     header_name="Data Snapshot",
    #     hide=False,
    #     wrapText=True,  # noqa
    # )  # noqa
    gb.configure_column(
        "label_explanation",
        header_name="Descrição",
        cellStyle={"white-space": "normal"},
        autoHeight=True,
        wrapText=True,
        hide=True,
    )
    # gb.configure_column("old_snapshot", header_name="Predição Desatualizada")
    gb.configure_side_bar()
    gb.configure_selection("single", use_checkbox=False)
    gb.configure_grid_options(enableCellTextSelection=True)
    # gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=20)  # noqa
    grid_options = gb.build()
    grid_response = AgGrid(
        table,
        gridOptions=grid_options,
        columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
        update_mode=GridUpdateMode.MODEL_CHANGED | GridUpdateMode.COLUMN_RESIZED,  # noqa
        # fit_columns_on_grid_load=True,
        height=533,
        custom_css={
            "#gridToolBar": {
                "padding-bottom": "0px !important",
            }
        },
    )

    selected_row = grid_response["selected_rows"]

    return selected_row


def create_order_column(table):
    # dict with the order of the labels from the worst to the best
    order = {
        "road_blockade": [
            "totally",
            "partially",
            "free",
        ],
        "traffic": [
            "impossible",
            "difficult",
            "moderate",
            "easy",
        ],
        "rain": [
            "true",
            "false",
        ],
        "water_level": [
            "high",
            "medium",
            "low",
        ],
        "image_corrupted": [
            "true",
            "false",
        ],
    }

    # create a column order with the following rules:
    # 1. if the object is not in the order keys dict, return 99
    # 2. if the object is in the order keys, return the index of the label in the order list

    # knowing that the dataframe always have the columns object and label, we can use the following code
    table["order"] = table.apply(
        lambda row: order.get(row["object"], 99).index(row["label"]), axis=1
    )

    return table


def get_identifications_index(identifications: list, fake_index: int):
    identifications_snapshots_to_index = {}
    current_index = 1
    cycle = 1
    for identification in identifications:
        url = identification["snapshot"]["image_url"]
        if url not in identifications_snapshots_to_index:
            identifications_snapshots_to_index[url] = {
                "index": current_index,
                "total": fake_index,
                "cycle": cycle,
            }
            if current_index == fake_index:
                cycle += 1
            current_index = (current_index % fake_index) + 1  # Cycle through numbers until N

    last_cycle = identifications_snapshots_to_index[
        list(identifications_snapshots_to_index.keys())[-1]
    ]["cycle"]
    for url in identifications_snapshots_to_index.keys():
        if identifications_snapshots_to_index[url]["cycle"] == last_cycle:
            identifications_snapshots_to_index[url]["total"] = (
                len(identifications_snapshots_to_index) % fake_index
            )
    return identifications_snapshots_to_index
