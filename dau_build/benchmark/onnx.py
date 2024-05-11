import torch
from finn.transformation.fpgadataflow.set_exec_mode import SetExecMode
from finn.util.fpgadataflow import is_hls_node, is_rtl_node
from io import BytesIO
from onnxruntime import InferenceSession, SessionOptions
from qonnx.core.onnx_exec import execute_onnx
from typing import Literal, Union

from ..finn import ModelWrapper

__all__ = (
    "get_inference_session",
    "infer",
    "infer_file",
    "infer_model",
)

EXEC_MODE = Literal["rtlsim", "cppsim"]


def get_inference_session(onnx_file: Union[str, BytesIO]) -> InferenceSession:
    if isinstance(onnx_file, BytesIO):
        onnx_file = onnx_file.read()
    return InferenceSession(onnx_file, SessionOptions())


def infer(session: InferenceSession, current_batch: torch.Tensor) -> list:
    input_name = session.get_inputs()[0].name
    predicted_value_onnx = session.run(None, {input_name: current_batch.numpy()})[0]
    predicted_value_onnx_ort = torch.tensor(predicted_value_onnx)
    return predicted_value_onnx_ort.numpy().tolist()


def infer_file(onnx_file: str, current_batch: torch.Tensor) -> list:
    return infer(get_inference_session(onnx_file=onnx_file), current_batch=current_batch)


def infer_model(model: ModelWrapper, current_batch: torch.Tensor, exec_mode: EXEC_MODE = "cppsim"):
    for node in model.graph.node:
        if is_hls_node(node) or is_rtl_node(node):
            # transform for simulation
            model = model.transform(SetExecMode(exec_mode))
            break
    try:
        output_dict = execute_onnx(model, {"global_in": current_batch})
        return list(output_dict.values())[0]
    except Exception as e:
        if e.args and "unspecified tensor shapes" in e.args[0]:
            raise RuntimeError("Model not cleaned!") from e
        raise
