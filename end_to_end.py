import os
import os.path
import torch
from logging import getLogger

from dau_build.benchmark import run_all_benchmarks
from dau_build.data import simple_positive_sine, simple_positive_sine_series, simple_positive_sine_training_batches
from dau_build.finn import (
    cleanup_model,
    convert_qonnx_to_finn,
    convert_to_hw,
    create_dataflow_partition,
    export_qonnx,
    specialize_hw_layers,
    streamline_model,
    tidy_model,
)
from dau_build.models import basic_training, simple_model
from dau_build.utilities import build_directory, setup_logging, silence_warnings

BUILD_DIR = build_directory()
setup_logging()
silence_warnings()

log = getLogger(__name__)

########
# DATA #
########
log.info("1 - Datagen")
batch_size = 20
X_train, y_train, X_test, y_test = simple_positive_sine()
train_series, test_series = simple_positive_sine_series()
train_dataset, train_labels = simple_positive_sine_training_batches(batch_size=batch_size)

#########
# MODEL #
#########
log.info("2 - Build model")
model = simple_model(n_neurons=4, look_back=batch_size)

#########
# TRAIN #
#########
log.info("3 - Train model")
basic_training(model, train_dataset=train_dataset, train_labels=train_labels)

###################
# Benchmark Model #
###################
log.info("4 - Benchmark model")
v1_graph_out = os.path.join(BUILD_DIR, "1.png")
run_all_benchmarks(
    model=model,
    test_series=test_series,
    train_series=train_series,
    batch_size=batch_size,
    X_train=X_train,
    y_train=y_train,
    X_test=X_test,
    y_test=y_test,
    to_file=v1_graph_out,
    label="Brevitas prediction",
)

###################
# EXPORT TO QONNX #
###################
log.info("5 - Convert to QONNX")
model = export_qonnx(model=model, name="simplenet", build_dir=BUILD_DIR, input_t=torch.randn(1, 20, dtype=torch.float32))
log.info("6 - Cleanup")
model = cleanup_model(model)

#########################
# Benchmark QONNX Model #
#########################
log.info("7 - Benchmark QONNX model")
v2_graph_out = os.path.join(BUILD_DIR, "2.png")
run_all_benchmarks(
    model=model,
    test_series=test_series,
    train_series=train_series,
    batch_size=batch_size,
    X_train=X_train,
    y_train=y_train,
    X_test=X_test,
    y_test=y_test,
    to_file=v2_graph_out,
    label="QONNX prediction",
)

# ###################
# # CONVERT TO FINN #
# ###################
log.info("8 - Convert QONNX model to FINN")
v3_graph_out = os.path.join(BUILD_DIR, "3.png")
model = convert_qonnx_to_finn(model)
# set_model_input_datatype(model, DataType["INT32"])

run_all_benchmarks(
    model=model,
    test_series=test_series,
    train_series=train_series,
    batch_size=batch_size,
    X_train=X_train,
    y_train=y_train,
    X_test=X_test,
    y_test=y_test,
    to_file=v3_graph_out,
    label="FINN base prediction",
)

#############
# TRANSFORM #
#############
log.info("9 - Transform FINN model for Hardware")
for i, foo in enumerate((tidy_model, streamline_model, convert_to_hw, create_dataflow_partition)):
    log.info(f"9 - Transform FINN model - {foo.__name__}")
    model = foo(model)
    graph_out = os.path.join(BUILD_DIR, f"{4+i}.png")
    run_all_benchmarks(
        model=model,
        test_series=test_series,
        train_series=train_series,
        batch_size=batch_size,
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        to_file=graph_out,
        label=f"FINN transform {i+1} prediction",
    )

########################
# Specialize HW Layers #
########################
log.info("10 - Specialize HW Layers")
child_model = specialize_hw_layers(model)
v8_graph_out = os.path.join(BUILD_DIR, "8.png")
run_all_benchmarks(
    model=child_model,
    test_series=test_series,
    train_series=train_series,
    batch_size=batch_size,
    X_train=X_train,
    y_train=y_train,
    X_test=X_test,
    y_test=y_test,
    to_file=v8_graph_out,
    label=f"FINN transform {i+1} prediction",
)

# ###############
# # Build Board #
# ###############
# from finn.util.basic import pynq_part_map

# pynq_board = "Pynq-Z1"
# fpga_part = pynq_part_map[pynq_board]
# target_clk_ns = 10

# from finn.transformation.fpgadataflow.make_zynq_proj import ZynqBuild

# print("Step 7: Building Board")
# model = model.transform(ZynqBuild(platform=pynq_board, period_ns=target_clk_ns))


# ################
# # Build Driver #
# ################
# from finn.transformation.fpgadataflow.make_pynq_driver import MakePYNQDriver

# print("Step 8: Building Driver")
# model = model.transform(MakePYNQDriver("zynq-iodma"))


# ###########
# # Package #
# ###########
# print("Step 9: Preparing Board Files")
# from datetime import datetime
# from distutils.dir_util import copy_tree
# from finn.util.basic import make_build_dir
# from shutil import copy

# # create directory for deployment files
# deployment_dir = make_build_dir(prefix="pynq_deployment_")
# model.set_metadata_prop("pynq_deployment_dir", deployment_dir)

# # get and copy necessary files
# # .bit and .hwh file
# bitfile = model.get_metadata_prop("bitfile")
# hwh_file = model.get_metadata_prop("hw_handoff")
# deploy_files = [bitfile, hwh_file]

# for dfile in deploy_files:
#     if dfile is not None:
#         copy(dfile, deployment_dir)

# # driver.py and python libraries
# pynq_driver_dir = model.get_metadata_prop("pynq_driver_dir")
# copy_tree(pynq_driver_dir, deployment_dir)

# iname = model.graph.input[0].name
# ishape = model.get_tensor_shape(iname)
# print("\tExpected network input shape is " + str(ishape))


# #######
# # Zip #
# #######
# print("Step 10: Zipping artifacts")
# from shutil import make_archive

# zip = f"deploy-on-pynq-simplenet-{datetime.now().strftime('%Y%m%d-%H-%M-%S')}"
# make_archive(zip, "zip", deployment_dir)
# print(f"Assets ready: {zip}")
