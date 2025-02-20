# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# --------------------------------------------------------------------------
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Union

import onnx
import torch

from olive.common.config_utils import validate_config
from olive.common.utils import tensor_data_to_device
from olive.hardware.accelerator import AcceleratorSpec
from olive.model import CompositeOnnxModel, ONNXModel, PyTorchModel
from olive.model.hf_utils import get_hf_model_io_config
from olive.model.model_config import IOConfig
from olive.passes import Pass
from olive.passes.onnx.common import get_external_data_config, model_proto_to_olive_model
from olive.passes.pass_config import PassConfigParam

logger = logging.getLogger(__name__)


class TraceModelWrapper(torch.nn.Module):
    def __init__(self, model: torch.nn.Module):
        super().__init__()
        self.model = model

    def forward(self, *input_data, **input_dict):
        if isinstance(self.model(*input_data, **input_dict), dict):
            return [val for val in self.model(*input_data, **input_dict).values()]
        return self.model(*input_data, **input_dict)


class OnnxConversion(Pass):
    """Convert a PyTorch model to ONNX model using torch.onnx.export."""

    _requires_user_script = True

    @staticmethod
    def _default_config(accelerator_spec: AcceleratorSpec) -> Dict[str, PassConfigParam]:
        config = {
            "target_opset": PassConfigParam(
                type_=int, default_value=14, description="The version of the default (ai.onnx) opset to target."
            )
        }
        config.update(get_external_data_config())
        return config

    def _run_for_config(
        self, model: PyTorchModel, config: Dict[str, Any], output_model_path: str
    ) -> Union[ONNXModel, CompositeOnnxModel]:
        # check if the model has components
        if model.components:
            onnx_models = []
            component_names = []
            for component_name in model.components:
                component_model = model.get_component(component_name)
                component_output_path = Path(output_model_path).with_suffix("") / component_name
                onnx_models.append(self._run_for_config(component_model, config, str(component_output_path)))
                component_names.append(component_name)
            return CompositeOnnxModel(onnx_models, component_names, hf_config=model.hf_config)

        # get dummy inputs
        dummy_inputs = model.get_dummy_inputs()

        # get input and output names, and dynamic axes
        # priority: model.io_config > auto-generated io_config for HF models
        io_config = model.io_config
        if not io_config and (model.hf_config and not model.hf_config.components):
            logger.debug("Using hf config to get io_config for the model.")
            io_config = get_hf_model_io_config(
                model.hf_config.model_name, model.hf_config.task, model.hf_config.feature
            )
        assert io_config, "Cannot get io_config for the model. Please specify io_config or hf_config for the model."
        io_config = validate_config(io_config, IOConfig)
        input_names = io_config.input_names
        output_names = io_config.output_names
        dynamic_axes = io_config.dynamic_axes

        # convert the model
        pytorch_model = model.load_model()
        pytorch_model.eval()

        # TODO: add e2e test for model on cpu but data on gpu; model on gpu but data on cpu
        # put pytorch_model and dummy_inputs at the same device
        pytorch_model.to("cpu")
        dummy_inputs = tensor_data_to_device(dummy_inputs, "cpu")
        if isinstance(pytorch_model, torch.jit.RecursiveScriptModule):
            pytorch_model = TraceModelWrapper(pytorch_model)

        output_model_path = ONNXModel.resolve_path(output_model_path)

        # there might be multiple files created during export, so we need to track the dir
        # if there are other processes writing to the same dir, we might end up deleting files created by
        # other processes
        tmp_dir = tempfile.TemporaryDirectory(prefix="olive_tmp")
        tmp_dir_path = Path(tmp_dir.name)
        tmp_model_path = str(tmp_dir_path / Path(output_model_path).name)

        torch.onnx.export(
            pytorch_model,
            dummy_inputs,
            tmp_model_path,
            export_params=True,
            opset_version=config["target_opset"],
            input_names=input_names,
            output_names=output_names,
            dynamic_axes=dynamic_axes,
        )

        # load the model
        onnx_model = onnx.load(tmp_model_path)
        # the model is loaded into memory, so it's safe to delete previously exported file(s)
        tmp_dir.cleanup()

        # Workaround as described under IOConfig.string_to_int_dim_params: change numeric dim_param to dim_value
        if io_config.string_to_int_dim_params:
            for tensor in onnx_model.graph.output:
                for dim_proto in tensor.type.tensor_type.shape.dim:
                    if dim_proto.HasField("dim_param") and dim_proto.dim_param in io_config.string_to_int_dim_params:
                        dim_value = int(dim_proto.dim_param)
                        dim_proto.Clear()
                        dim_proto.dim_value = dim_value

        # save the model to the output path and return the model
        return model_proto_to_olive_model(onnx_model, output_model_path, config)
