from finn.transformation.streamline import Streamline
from finn.transformation.streamline.absorb import (
    AbsorbAddIntoMultiThreshold,
    AbsorbMulIntoMultiThreshold,
    AbsorbSignBiasIntoMultiThreshold,
    AbsorbTransposeIntoFlatten,
)
from finn.transformation.streamline.reorder import MoveMulPastDWConv, MoveScalarMulPastMatMul
from finn.transformation.streamline.round_thresholds import RoundAndClipThresholds
from logging import getLogger
from qonnx.transformation.double_to_single_float import DoubleToSingleFloat
from qonnx.transformation.general import RemoveUnusedTensors
from qonnx.transformation.infer_data_layouts import InferDataLayouts
from qonnx.transformation.remove import RemoveIdentityOps

__all__ = (
    "Streamline",
    "AbsorbAddIntoMultiThreshold",
    "AbsorbMulIntoMultiThreshold",
    "AbsorbSignBiasIntoMultiThreshold",
    "AbsorbTransposeIntoFlatten",
    "MoveMulPastDWConv",
    "MoveScalarMulPastMatMul",
    "RoundAndClipThresholds",
    "DoubleToSingleFloat",
    "RemoveUnusedTensors",
    "InferDataLayouts",
    "RemoveIdentityOps",
    "streamline_model",
)

log = getLogger(__name__)


def streamline_model(model):
    for t in (
        AbsorbSignBiasIntoMultiThreshold,
        Streamline,
        InferDataLayouts,
        RemoveUnusedTensors,
        DoubleToSingleFloat,
        MoveMulPastDWConv,
        AbsorbTransposeIntoFlatten,
        AbsorbAddIntoMultiThreshold,
        AbsorbMulIntoMultiThreshold,
        InferDataLayouts,
        MoveScalarMulPastMatMul,
        RemoveIdentityOps,
        RoundAndClipThresholds,
    ):
        log.info(f"Transforming model with {t.__name__}")
        model = model.transform(t())
    return model
