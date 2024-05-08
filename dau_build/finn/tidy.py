# from qonnx.core.datatype import DataType
from qonnx.transformation.fold_constants import FoldConstants
from qonnx.transformation.general import GiveReadableTensorNames, GiveUniqueNodeNames, RemoveStaticGraphInputs, RemoveUnusedTensors
from qonnx.transformation.infer_data_layouts import InferDataLayouts
from qonnx.transformation.infer_datatypes import InferDataTypes
from qonnx.transformation.infer_shapes import InferShapes

# print("Step 3: Tidying Model")
# global_inp_name = model.graph.input[0].name
# model.set_tensor_datatype(global_inp_name, DataType["INT32"])


def tidy_model(model):
    model = model.transform(InferShapes())
    model = model.transform(FoldConstants())
    # model = model.transform(InsertTopK())
    # model = model.transform(AbsorbScalarMulAddIntoTopK())
    model = model.transform(InferShapes())
    model = model.transform(InferDataTypes())
    model = model.transform(InferDataLayouts())
    model = model.transform(GiveUniqueNodeNames())
    model = model.transform(GiveReadableTensorNames())
    model = model.transform(RemoveStaticGraphInputs())
    model = model.transform(RemoveUnusedTensors())
    return model
