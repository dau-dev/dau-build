from logging import getLogger
from qonnx.core.datatype import DataType
from qonnx.core.modelwrapper import ModelWrapper

__all__ = ("ModelWrapper", "DataType", "set_model_input_datatype")

log = getLogger(__name__)


def set_model_input_datatype(model: ModelWrapper, dtype: DataType = DataType["INT32"]):
    global_inp_name = model.graph.input[0].name
    log.info(f"Setting model input\tfrom: {model.get_tensor_datatype(global_inp_name)}\tto: {dtype}")
    model.set_tensor_datatype(global_inp_name, dtype)
    return model
