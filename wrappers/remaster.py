import os
from typing import Any, List, Dict

import matchering as mg

from util.data_classes import ProjectFiles
from wrappers.base_wrapper import BaseWrapper, TypedInput


class Remaster(BaseWrapper):
    title = "Remaster"
    priority = 4
    allowed_kwargs = {
        "reference": TypedInput(
            description="Reference track",
            default=None,
            type=str,
            gradio_type="File"
        )
    }

    def process_audio(self, inputs: List[ProjectFiles], callback=None, **kwargs: Dict[str, Any]) -> List[ProjectFiles]:
        reference_file = kwargs.get("reference")
        if not reference_file:
            raise ValueError("Reference track not provided")
        callback(0, f"Remastering {len(inputs)} audio files", len(inputs))
        callback_step = 0
        pj_outputs = []
        for project in inputs:
            outputs = []
            output_folder = os.path.join(project.project_dir, "remastered")
            os.makedirs(output_folder, exist_ok=True)
            input_files, _ = self.filter_inputs(project, "audio")
            for input_file in input_files:
                callback(callback_step, f"Remastering {input_file}", len(inputs))
                inputs_name, inputs_ext = os.path.splitext(os.path.basename(input_file))
                output_file = os.path.join(output_folder, f"{inputs_name}_remastered{inputs_ext}")
                mg.process(
                    # The track you want to master
                    target=input_file,
                    # Some "wet" reference track
                    reference=reference_file,
                    # Where and how to save your results
                    results=[
                        mg.pcm24(output_file),
                    ],
                )
                outputs.append(output_file)
                callback_step += 1
            project.add_output("remaster", outputs)
            pj_outputs.append(project)
        return pj_outputs

    def register_api_endpoint(self, api) -> Any:
        pass