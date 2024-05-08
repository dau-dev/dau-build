from finn.transformation.fpgadataflow.specialize_layers import SpecializeLayers
from qonnx.core.modelwrapper import ModelWrapper
from qonnx.custom_op.registry import getCustomOp


def specialize_hw_layers(parent_model):
    sdp_node = parent_model.get_nodes_by_op_type("StreamingDataflowPartition")[0]
    sdp_node = getCustomOp(sdp_node)
    dataflow_model_filename = sdp_node.get_nodeattr("model")
    model = ModelWrapper(dataflow_model_filename)

    print("Step 6: Specialize HW Layers")

    model = model.transform(SpecializeLayers())
    return model
