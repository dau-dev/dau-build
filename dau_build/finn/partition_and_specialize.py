from logging import getLogger

from finn.transformation.fpgadataflow.create_dataflow_partition import CreateDataflowPartition
from finn.transformation.fpgadataflow.specialize_layers import SpecializeLayers
from qonnx.core.modelwrapper import ModelWrapper
from qonnx.custom_op.registry import getCustomOp

__all__ = (
    "SpecializeLayers",
    "CreateDataflowPartition",
    "create_dataflow_partition",
    "specialize_hw_layers",
)

log = getLogger(__name__)


def create_dataflow_partition(model):
    log.info("Creating dataflow partition")
    return model.transform(CreateDataflowPartition())


def specialize_hw_layers(parent_model):
    log.info("Specializing Hardware Layers")
    sdp_node = parent_model.get_nodes_by_op_type("StreamingDataflowPartition")[0]
    sdp_node = getCustomOp(sdp_node)
    dataflow_model_filename = sdp_node.get_nodeattr("model")
    model = ModelWrapper(dataflow_model_filename)
    model = model.transform(SpecializeLayers())
    return model
