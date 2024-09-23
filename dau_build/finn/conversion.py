from logging import getLogger

from brevitas.export import export_qonnx as export_qonnx_base
from finn.transformation.qonnx.convert_qonnx_to_finn import ConvertQONNXtoFINN
from torch import tensor
from torch.nn import Module

from ..utilities import model_path
from .common import ModelWrapper

__all__ = (
    "ConvertQONNXtoFINN",
    "export_qonnx",
    "convert_qonnx_to_finn",
)

log = getLogger(__name__)


def convert_qonnx_to_finn(model):
    log.info("Converting QONNX model to FINN model")
    return model.transform(ConvertQONNXtoFINN())


def export_qonnx(model: Module, name: str, build_dir: str, input_t: tensor) -> ModelWrapper:
    path = model_path(build_dir, f"1_{name}_qonnx")
    log.info(f"Exporting model {name} to file {path}")
    return ModelWrapper(
        export_qonnx_base(
            model,
            input_t=input_t,
            export_path=path,
            opset_version=9,
        )
    )
