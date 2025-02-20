import json
import os
import random
import zipfile

import gradio as gr
import requests

from handlers.args import ArgHandler
from handlers.config import model_path
from modules.yue.inference.infer import generate_music
from modules.yue.inference.xcodec_mini_infer.utils.utils import seed_everything

SEND_TO_PROCESS_BUTTON: gr.Button = None
OUTPUT_MIX: gr.Audio = None
arg_handler = ArgHandler()
# Language mapping for selecting the correct Stage 1 model
STAGE1_MODELS = {
    "English": {
        "cot": "m-a-p/YuE-s1-7B-anneal-en-cot",
        "icl": "m-a-p/YuE-s1-7B-anneal-en-icl"
    },
    "Mandarin/Cantonese": {
        "cot": "m-a-p/YuE-s1-7B-anneal-zh-cot",
        "icl": "m-a-p/YuE-s1-7B-anneal-zh-icl"
    },
    "Japanese/Korean": {
        "cot": "m-a-p/YuE-s1-7B-anneal-jp-kr-cot",
        "icl": "m-a-p/YuE-s1-7B-anneal-jp-kr-icl"
    }
}

base_model_url = "https://github.com/d8ahazard/AudioLab/releases/download/1.0.0/YuE_models.zip"


def fetch_and_extxract_models():
    model_dir = os.path.join(model_path, "YuE")
    if not os.path.exists(model_dir):
        os.makedirs(model_dir, exist_ok=True)
    files_to_check = ["hf_1_325000", "ckpt_00360000.pth", "config.yaml", "config_decoder.yaml", "decoder_131000.pth",
                      "decoder_151000.pth", "tokenizer.model"]
    if not all([os.path.exists(os.path.join(model_dir, f)) for f in files_to_check]):
        model_dl = os.path.join(model_dir, "YuE_models.zip")
        if os.path.exists(model_dl):
            os.remove(model_dl)
        with open(model_dl, "wb") as f:
            f.write(requests.get(base_model_url).content)
        with zipfile.ZipFile(os.path.join(model_dir, "YuE_models.zip"), 'r') as zip_ref:
            zip_ref.extractall(model_dir)
        # Delete the zip file
        os.remove(model_dl)


def render(arg_handler: ArgHandler):
    global SEND_TO_PROCESS_BUTTON, OUTPUT_MIX
    with gr.Blocks() as app:
        gr.Markdown("## YuE Music Generation")

        with gr.Row():
            # Left Column - Settings
            with gr.Column():
                gr.Markdown("### 🔧 Settings")
                model_language = gr.Dropdown(
                    ["English", "Mandarin/Cantonese", "Japanese/Korean"],
                    value="English",
                    label="Model Language",
                    elem_classes="hintitem", elem_id="yue_model_language", key="yue_model_language"
                )
                max_new_tokens = gr.Slider(
                    500, 5000, value=3000, step=100,
                    label="Max New Tokens",
                    elem_classes="hintitem", elem_id="yue_max_new_tokens", key="yue_max_new_tokens"
                )
                run_n_segments = gr.Slider(
                    1, 10, value=2, step=1,
                    label="Run N Segments",
                    elem_classes="hintitem", elem_id="yue_run_n_segments", key="yue_run_n_segments"
                )
                stage2_batch_size = gr.Slider(
                    1, 8, value=4, step=1,
                    label="Stage 2 Batch Size",
                    elem_classes="hintitem", elem_id="yue_stage2_batch_size", key="yue_stage2_batch_size"
                )
                keep_intermediate = gr.Checkbox(
                    label="Keep Intermediate Files",
                    value=False,
                    elem_classes="hintitem", elem_id="yue_keep_intermediate", key="yue_keep_intermediate"
                )
                disable_offload_model = gr.Checkbox(
                    label="Disable Model Offloading",
                    value=False,
                    elem_classes="hintitem", elem_id="yue_disable_offload_model", key="yue_disable_offload_model"
                )
                rescale = gr.Checkbox(
                    label="Rescale Output",
                    elem_classes="hintitem", elem_id="yue_rescale", key="yue_rescale"
                )
                cuda_idx = gr.Number(
                    value=0, label="CUDA Index",
                    elem_classes="hintitem", elem_id="yue_cuda_idx", key="yue_cuda_idx"
                )
                seed = gr.Slider(
                    value=-1, label="Seed",
                    minimum=-1, maximum=4294967295, step=1,
                    elem_classes="hintitem", elem_id="yue_seed", key="yue_seed"
                )

            # Middle Column - Input Data
            with gr.Column():
                gr.Markdown("### 🎤 Inputs")
                genre_txt = gr.Textbox(
                    label="Genre Tags",
                    placeholder="e.g., uplifting pop airy vocal electronic bright",
                    lines=2,
                    elem_classes="hintitem", elem_id="yue_genre_txt", key="yue_genre_txt"
                )
                lyrics_txt = gr.Textbox(
                    label="Lyrics",
                    placeholder="Enter structured lyrics here... (Use [verse], [chorus] labels)",
                    lines=10,
                    elem_classes="hintitem", elem_id="yue_lyrics_txt", key="yue_lyrics_txt"
                )
                use_audio_prompt = gr.Checkbox(
                    label="Use Audio Reference (ICL Mode)",
                    elem_classes="hintitem", elem_id="yue_use_audio_prompt", key="yue_use_audio_prompt"
                )
                with gr.Row():
                    audio_prompt_path = gr.File(
                        label="Reference Audio File (Optional)",
                        elem_classes="hintitem", elem_id="yue_audio_prompt_path", key="yue_audio_prompt_path"
                    )
                    prompt_start_time = gr.Number(
                        value=0.0, label="Prompt Start Time (sec)",
                        elem_classes="hintitem", elem_id="yue_prompt_start_time", key="yue_prompt_start_time"
                    )
                    prompt_end_time = gr.Number(
                        value=30.0, label="Prompt End Time (sec)",
                        elem_classes="hintitem", elem_id="yue_prompt_end_time", key="yue_prompt_end_time"
                    )

            # Right Column - Start & Outputs
            with gr.Column():
                gr.Markdown("### 🎶 Outputs")
                with gr.Row():
                    start_button = gr.Button(
                        "Generate Music",
                        elem_classes="hintitem",
                        elem_id="yue_start_button",
                        key="yue_start_button",
                        variant="primary"
                    )
                    SEND_TO_PROCESS_BUTTON = gr.Button(
                        value="Send to Process",
                        variant="secondary",
                        elem_classes="hintitem", elem_id="yue_send_to_process", key="yue_send_to_process"
                    )
                OUTPUT_MIX = gr.Audio(
                    label="Final Mix",
                    elem_classes="hintitem", elem_id="yue_output_mix", key="yue_output_mix",
                    type="filepath",
                    sources=None,
                    interactive=False
                )
                output_vocal = gr.Audio(
                    label="Vocal Output",
                    elem_classes="hintitem", elem_id="yue_output_vocal", key="yue_output_vocal",
                    sources=None,
                    interactive=False
                )
                output_inst = gr.Audio(
                    label="Instrumental Output",
                    elem_classes="hintitem", elem_id="yue_output_inst", key="yue_output_inst",
                    sources=None,
                    interactive=False
                )

    return app


def listen():
    process_inputs = arg_handler.get_element("main", "process_inputs")
    if process_inputs:
        SEND_TO_PROCESS_BUTTON.click(fn=send_to_process, inputs=[OUTPUT_MIX, process_inputs], outputs=process_inputs)


def send_to_process(output_mix, process_inputs):
    if not output_mix or not os.path.exists(output_mix):
        return gr.update()
    if output_mix in process_inputs:
        return gr.update()
    process_inputs.append(output_mix)
    return gr.update(value=process_inputs)


def register_descriptions(arg_handler: ArgHandler):
    descriptions = {
        "model_language": "Select the language of the model to use for generation.",
        "use_audio_prompt": "Check this box if you want to use an audio reference for generation.",
        "genre_txt": "Enter genre tags to guide the music generation. Use spaces to separate multiple tags.",
        "lyrics_txt": "Enter structured lyrics with [verse], [chorus], [bridge] labels. Separate lines with '\\n'.",
        "audio_prompt_path": "Upload an audio file to use as a reference for generation.",
        "prompt_start_time": "Specify the start time in seconds for the audio prompt.",
        "prompt_end_time": "Specify the end time in seconds for the audio prompt.",
        "max_new_tokens": "Set the maximum number of tokens to generate.",
        "run_n_segments": "Specify how many segments to run during generation.",
        "stage2_batch_size": "Set the batch size for Stage 2 of generation.",
        "keep_intermediate": "Check this box to keep intermediate files generated during processing.",
        "disable_offload_model": "Check this box to disable model offloading and run everything on CPU.",
        "cuda_idx": "Specify the CUDA index to use for GPU processing.",
        "rescale": "Check this box to rescale the output audio files.",
        "seed": "Use -1 for random, or specify a seed for reproducibility."
    }
    for elem_id, description in descriptions.items():
        arg_handler.register_description("yue", elem_id, description)
