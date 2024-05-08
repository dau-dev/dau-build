from finn.transformation.streamline import Streamline
from finn.transformation.streamline.absorb import (
    AbsorbAddIntoMultiThreshold,
    AbsorbMulIntoMultiThreshold,
    AbsorbSignBiasIntoMultiThreshold,
    AbsorbTransposeIntoFlatten,
)
from finn.transformation.streamline.reorder import MoveMulPastDWConv, MoveScalarMulPastMatMul
from finn.transformation.streamline.round_thresholds import RoundAndClipThresholds
from qonnx.transformation.double_to_single_float import DoubleToSingleFloat
from qonnx.transformation.general import RemoveUnusedTensors
from qonnx.transformation.infer_data_layouts import InferDataLayouts
from qonnx.transformation.remove import RemoveIdentityOps

print("Step 4: Streamlining Model")


def streamline_model(model):
    model = model.transform(AbsorbSignBiasIntoMultiThreshold())
    model = model.transform(Streamline())
    model = model.transform(InferDataLayouts())
    model = model.transform(RemoveUnusedTensors())
    model = model.transform(DoubleToSingleFloat())
    model = model.transform(MoveMulPastDWConv())
    model = model.transform(AbsorbTransposeIntoFlatten())
    model = model.transform(AbsorbAddIntoMultiThreshold())
    model = model.transform(AbsorbMulIntoMultiThreshold())
    model = model.transform(InferDataLayouts())
    model = model.transform(MoveScalarMulPastMatMul())
    model = model.transform(RemoveIdentityOps())
    model = model.transform(RoundAndClipThresholds())
    return model
