import os
import os.path
import torch
from finn.builder.build_dataflow import DataflowBuildConfig, build_dataflow_cfg

# from finn.builder.build_dataflow_config import DataflowOutputType, ShellFlowType, VerificationStepType
from finn.builder.build_dataflow_config import DataflowOutputType, ShellFlowType
from logging import getLogger

from dau_build.benchmark import run_all_benchmarks
from dau_build.data import simple_positive_sine, simple_positive_sine_series, simple_positive_sine_training_batches
from dau_build.finn import cleanup_model, export_qonnx
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

##################
# Run Build Flow #
##################
log.info("8 - Running E2E Build flow")
# https://github.com/Xilinx/finn/blob/dev/notebooks/end2end_example/cybersecurity/3-build-accelerator-with-finn.ipynb
cfg = DataflowBuildConfig(
    output_dir=BUILD_DIR,
    mvau_wwidth_max=80,
    target_fps=1000000,
    synth_clk_period_ns=10.0,
    board="Pynq-Z1",
    shell_flow_type=ShellFlowType.VIVADO_ZYNQ,
    steps=[
        # https://github.com/Xilinx/finn/blob/e3087ad9fbabcc35f21164d415ababec4f462e9f/src/finn/builder/build_dataflow_config.py#L106
        "step_qonnx_to_finn",
        "step_tidy_up",
        "step_streamline",
        "step_convert_to_hw",
        "step_create_dataflow_partition",
        "step_specialize_layers",
        "step_target_fps_parallelization",
        "step_apply_folding_config",
        "step_minimize_bit_width",
        "step_generate_estimate_reports",
        "step_hw_codegen",
        "step_hw_ipgen",
        "step_set_fifo_depths",
        "step_create_stitched_ip",
        "step_measure_rtlsim_performance",
        "step_out_of_context_synthesis",
        "step_synthesize_bitfile",
        "step_make_pynq_driver",
        "step_deployment_package",
    ],
    verify_steps=[
        # VerificationStepType.QONNX_TO_FINN_PYTHON,
        # VerificationStepType.TIDY_UP_PYTHON,
        # VerificationStepType.STREAMLINED_PYTHON,
        # VerificationStepType.FOLDED_HLS_CPPSIM,
        # VerificationStepType.STITCHED_IP_RTLSIM,
    ],
    generate_outputs=[
        DataflowOutputType.ESTIMATE_REPORTS,
        DataflowOutputType.STITCHED_IP,
        DataflowOutputType.RTLSIM_PERFORMANCE,
        DataflowOutputType.OOC_SYNTH,
        DataflowOutputType.BITFILE,
        DataflowOutputType.PYNQ_DRIVER,
        DataflowOutputType.DEPLOYMENT_PACKAGE,
    ],
)

build_dataflow_cfg(os.path.join(BUILD_DIR, "1_simplenet_qonnx.onnx"), cfg)
