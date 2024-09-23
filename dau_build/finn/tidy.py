from logging import getLogger

from qonnx.transformation.fold_constants import FoldConstants
from qonnx.transformation.general import GiveReadableTensorNames, GiveUniqueNodeNames, RemoveStaticGraphInputs, RemoveUnusedTensors
from qonnx.transformation.infer_data_layouts import InferDataLayouts
from qonnx.transformation.infer_datatypes import InferDataTypes
from qonnx.transformation.infer_shapes import InferShapes

__all__ = (
    "FoldConstants",
    "GiveUniqueNodeNames",
    "InferDataLayouts",
    "InferDataTypes",
    "InferShapes",
    "RemoveStaticGraphInputs",
    "RemoveUnusedTensors",
    "tidy_model",
)

log = getLogger(__name__)


def tidy_model(model):
    for t in (
        InferShapes,
        FoldConstants,
        # InsertTopK
        # AbsorbScalarMulAddIntoTopK
        InferShapes,
        InferDataTypes,
        InferDataLayouts,
        GiveUniqueNodeNames,
        GiveReadableTensorNames,
        RemoveStaticGraphInputs,
        RemoveUnusedTensors,
    ):
        log.info(f"Transforming model with {t.__name__}")
        model = model.transform(t())
    return model
