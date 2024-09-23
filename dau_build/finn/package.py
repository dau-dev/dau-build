from datetime import datetime
from distutils.dir_util import copy_tree
from shutil import copy, make_archive

from finn.util.basic import make_build_dir


def package(model):
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

    zip = f"deploy-on-pynq-simplenet-{datetime.now().strftime('%Y%m%d-%H-%M-%S')}"
    make_archive(zip, "zip", deployment_dir)
    print(f"Assets ready: {zip}")
