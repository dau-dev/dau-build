# ruff: noqa
import numpy as np
import os
import torch
from tqdm import tqdm

build_dir = os.environ["FINN_BUILD_DIR"] = os.path.join(os.path.abspath(os.curdir), "finn_build")

########
# DATA #
########
X_train = np.arange(0, 100, 0.5).astype(np.float32)
y_train = ((np.sin(X_train) + 1.0) * 128).astype(np.float32)
X_test = np.arange(100, 200, 0.5).astype(np.float32)
y_test = ((np.sin(X_test) + 1.0) * 128).astype(np.float32)
train_series = torch.from_numpy(y_train).type(torch.float32)
test_series = torch.from_numpy(y_test).type(torch.float32)
# expects input of (batch, sequence, features)
# So shape should be (1, 180, 20) and labels (1, 1, 180)
look_back = 20

train_dataset = []
train_labels = []
for i in range(len(train_series) - look_back):
    train_dataset.append(train_series[i : i + 20])
    train_labels.append(train_series[i + 20])

train_dataset = torch.stack(train_dataset).unsqueeze(0)
train_labels = torch.stack(train_labels).unsqueeze(0).unsqueeze(2)


import brevitas.nn as qnn

#########
# MODEL #
#########
import torch.nn as nn
import torch.optim as optim
from brevitas.quant import Int8WeightPerTensorFixedPoint


class QuantNet(nn.Module):
    def __init__(self, n_neurons, input_shape):
        super(QuantNet, self).__init__()
        self.quant_inp = qnn.QuantIdentity(bit_width=8, return_quant_tensor=True)
        self.fc1 = qnn.QuantLinear(
            input_shape,
            n_neurons,
            weight_quant=Int8WeightPerTensorFixedPoint,
            return_quant_tensor=True,
            bias=False,
        )
        # self.relu1 = qnn.QuantReLU(bit_width=4, return_quant_tensor=True)
        self.fc2 = qnn.QuantLinear(n_neurons, 1, weight_quant=Int8WeightPerTensorFixedPoint, bias=False)

    def forward(self, x):
        out = self.quant_inp(x)
        out = self.fc1(out)
        # out = self.relu1(out)
        out = self.fc2(out)
        return out


n_neurons = 4
model = QuantNet(n_neurons, look_back)

#########
# TRAIN #
#########
loss_function = nn.MSELoss()
optimizer = optim.Adam(model.parameters(), lr=0.001)

loss_curve = []
for epoch in tqdm(range(300)):
    loss_total = 0

    model.zero_grad()

    predictions = model(train_dataset)

    loss = loss_function(predictions, train_labels)
    loss_total += loss.item()
    loss.backward()
    optimizer.step()
    loss_curve.append(loss_total)


###################
# EXPORT TO QONNX #
###################
from brevitas.export import export_qonnx

model = export_qonnx(
    model,
    input_t=torch.randn(1, 20, dtype=torch.float32),
    export_path="onnx/1_simplenet_qonnx.onnx",
    opset_version=9,
)

from qonnx.core.modelwrapper import ModelWrapper

model = ModelWrapper(model)

#########
# CLEAN #
#########
from qonnx.util.cleanup import cleanup_model

print("Step 1: Cleaning up model")
model = cleanup_model(model)

###################
# CONVERT TO FINN #
###################
from finn.transformation.qonnx.convert_qonnx_to_finn import ConvertQONNXtoFINN

print("Step 2: Converting from QONNX to FINN")
model = model.transform(ConvertQONNXtoFINN())


# from qonnx.transformation.insert_topk import InsertTopK
from finn.transformation.streamline.absorb import AbsorbScalarMulAddIntoTopK

#############
# TRANSFORM #
#############
from qonnx.core.datatype import DataType
from qonnx.transformation.fold_constants import FoldConstants
from qonnx.transformation.general import GiveReadableTensorNames, GiveUniqueNodeNames, RemoveStaticGraphInputs, RemoveUnusedTensors
from qonnx.transformation.infer_data_layouts import InferDataLayouts
from qonnx.transformation.infer_datatypes import InferDataTypes
from qonnx.transformation.infer_shapes import InferShapes

print("Step 3: Tidying Model")
global_inp_name = model.graph.input[0].name
model.set_tensor_datatype(global_inp_name, DataType["INT32"])


def tidy_model(model):
    model = model.transform(InferShapes())
    model = model.transform(FoldConstants())
    # model = model.transform(InsertTopK())
    model = model.transform(AbsorbScalarMulAddIntoTopK())
    model = model.transform(InferShapes())
    model = model.transform(InferDataTypes())
    model = model.transform(InferDataLayouts())
    model = model.transform(GiveUniqueNodeNames())
    model = model.transform(GiveReadableTensorNames())
    model = model.transform(RemoveStaticGraphInputs())
    return model


model = tidy_model(model)

from finn.transformation.streamline import Streamline
from finn.transformation.streamline.absorb import (
    AbsorbAddIntoMultiThreshold,
    AbsorbMulIntoMultiThreshold,
    AbsorbSignBiasIntoMultiThreshold,
    AbsorbTransposeIntoFlatten,
)
from finn.transformation.streamline.reorder import MoveMulPastDWConv, MoveScalarMulPastMatMul
from finn.transformation.streamline.round_thresholds import RoundAndClipThresholds

##############
# STREAMLINE #
##############
from qonnx.transformation.double_to_single_float import DoubleToSingleFloat
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


model = streamline_model(model)
model = tidy_model(model)


#################
# HW Conversion #
#################
print("Step 4: Convert to HLS Layers")
import finn.transformation.fpgadataflow.convert_to_hw_layers as to_hw

model = model.transform(to_hw.InferQuantizedMatrixVectorActivation())
model = model.transform(to_hw.InferThresholdingLayer())
model = model.transform(to_hw.InferBinaryMatrixVectorActivation())

######################
# Dataflow Partition #
######################
from finn.transformation.fpgadataflow.create_dataflow_partition import CreateDataflowPartition

print("Step 5: Create Dataflow Partition")
parent_model = model.transform(CreateDataflowPartition())

########################
# Specialize HW Layers #
########################
from qonnx.custom_op.registry import getCustomOp

sdp_node = parent_model.get_nodes_by_op_type("StreamingDataflowPartition")[0]
sdp_node = getCustomOp(sdp_node)
dataflow_model_filename = sdp_node.get_nodeattr("model")
model = ModelWrapper(dataflow_model_filename)

print("Step 6: Specialize HW Layers")
from finn.transformation.fpgadataflow.specialize_layers import SpecializeLayers

model = model.transform(SpecializeLayers())

###############
# Build Board #
###############
from finn.util.basic import pynq_part_map

pynq_board = "Pynq-Z1"
fpga_part = pynq_part_map[pynq_board]
target_clk_ns = 10

from finn.transformation.fpgadataflow.make_zynq_proj import ZynqBuild

print("Step 7: Building Board")
model = model.transform(ZynqBuild(platform=pynq_board, period_ns=target_clk_ns))


################
# Build Driver #
################
from finn.transformation.fpgadataflow.make_pynq_driver import MakePYNQDriver

print("Step 8: Building Driver")
model = model.transform(MakePYNQDriver("zynq-iodma"))


###########
# Package #
###########
print("Step 9: Preparing Board Files")
from datetime import datetime
from distutils.dir_util import copy_tree
from finn.util.basic import make_build_dir
from shutil import copy

# create directory for deployment files
deployment_dir = make_build_dir(prefix="pynq_deployment_")
model.set_metadata_prop("pynq_deployment_dir", deployment_dir)

# get and copy necessary files
# .bit and .hwh file
bitfile = model.get_metadata_prop("bitfile")
hwh_file = model.get_metadata_prop("hw_handoff")
deploy_files = [bitfile, hwh_file]

for dfile in deploy_files:
    if dfile is not None:
        copy(dfile, deployment_dir)

# driver.py and python libraries
pynq_driver_dir = model.get_metadata_prop("pynq_driver_dir")
copy_tree(pynq_driver_dir, deployment_dir)

iname = model.graph.input[0].name
ishape = model.get_tensor_shape(iname)
print("\tExpected network input shape is " + str(ishape))


#######
# Zip #
#######
print("Step 10: Zipping artifacts")
from shutil import make_archive

zip = f"deploy-on-pynq-simplenet-{datetime.now().strftime('%Y%m%d-%H-%M-%S')}"
make_archive(zip, "zip", deployment_dir)
print(f"Assets ready: {zip}")
