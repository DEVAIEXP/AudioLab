import re
from abc import abstractmethod
from typing import List, Dict, Any, Union

import pydantic
import gradio as gr
from annotated_types import Ge, Le

from handlers.args import ArgHandler

if pydantic.__version__.startswith("1."):
    PYDANTIC_V2 = False
else:
    PYDANTIC_V2 = True


class TypedInput:
    def __init__(self, default: Any = ...,
                 description: str = None,
                 ge: float = None,
                 le: float = None,
                 min_length: int = None,
                 max_length: int = None,
                 regex: str = None,
                 choices: List[Union[str, int]] = None,
                 type: type = None,
                 gradio_type: str = None,
                 render: bool = True):
        field_kwargs = {
            "default": default,
            "description": description,
            "ge": ge,
            "le": le,
            "min_length": min_length,
            "max_length": max_length,
        }

        if PYDANTIC_V2:
            field_kwargs["pattern"] = regex
            if choices:
                field_kwargs["json_schema_extra"] = {"enum": choices}
        else:
            field_kwargs["regex"] = regex
            field_kwargs["enum"] = choices

        field = pydantic.Field(**field_kwargs)
        self.type = type
        self.field = field
        self.render = render
        self.gradio_type = gradio_type if gradio_type else self.pick_gradio_type()

    def pick_gradio_type(self):
        if self.type == bool:
            return "Checkbox"
        if self.type == str:
            return "Text"
        if self.type == int or self.type == float:
            try:
                if self.field.ge is not None and self.field.le is not None:
                    return "Slider"
            except:
                pass
        if self.type == float:
            return "Number"
        if self.type == list:
            return "Textfield"
        return "Text"


class BaseWrapper:
    _instance = None
    priority = 1000
    allowed_kwargs = {}
    description = "Base Wrapper"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BaseWrapper, cls).__new__(cls)
            cls._instance.arg_handler = ArgHandler()
            class_name = cls.__name__
            cls._instance.title = ' '.join(
                word.capitalize() for word in re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).split('_'))

        return cls._instance

    @abstractmethod
    def process_audio(self, inputs: List[str], callback=None, **kwargs: Dict[str, Any]) -> List[str]:
        pass

    @abstractmethod
    def register_api_endpoint(self, api) -> Any:
        pass

    def render_options(self, container: gr.Column):
        """
        Render the options for this wrapper into the provided container.
        """
        if not len(self.allowed_kwargs):
            return
        for key, value in self.allowed_kwargs.items():
            if value.render:
                with container:
                    self.create_gradio_element(self.__class__.__name__, key, value)

    def create_gradio_element(self, class_name: str, key: str, value: TypedInput):
        """
        Create and register a Gradio element for the specified input field.
        """
        arg_key = key
        key = " ".join([word.capitalize() for word in key.split("_")])

        # Extract `ge` and `le` from metadata if they exist
        ge_value = None
        le_value = None
        choices = None
        for meta in value.field.metadata:
            if isinstance(meta, Ge):
                ge_value = meta.ge
            elif isinstance(meta, Le):
                le_value = meta.le

        if value.field.json_schema_extra:
            for extra in value.field.json_schema_extra:
                if extra == "enum":
                    choices = value.field.json_schema_extra[extra]

        match value.gradio_type:
            case "Checkbox":
                elem = gr.Checkbox(label=key, value=value.field.default)
            case "Text":
                elem = gr.Textbox(label=key, value=value.field.default)
            case "Slider":
                elem = gr.Slider(
                    label=key,
                    value=value.field.default,
                    minimum=ge_value if ge_value is not None else 0,
                    maximum=le_value if le_value is not None else 100,
                    step=0.01
                )
            case "Number":
                elem = gr.Number(label=key, value=value.field.default)
            case "Textfield":
                elem = gr.Textbox(label=key, value=value.field.default, lines=3)
            case "Dropdown":
                elem = gr.Dropdown(label=key, choices=choices, value=value.field.default)
            case "File":
                elem = gr.File(label=key)
            case _:
                elem = gr.Textbox(label=key, value=value.field.default)

        self.arg_handler.register_element(class_name, arg_key, elem)
        return elem