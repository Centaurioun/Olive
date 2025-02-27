# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# --------------------------------------------------------------------------
import logging
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from olive.cache import get_local_path
from olive.evaluator.metric import Metric, joint_metric_key
from olive.evaluator.olive_evaluator import OliveEvaluatorFactory
from olive.hardware.accelerator import AcceleratorSpec
from olive.model import ONNXModel
from olive.passes import Pass
from olive.passes.onnx.common import get_external_data_config, model_proto_to_olive_model
from olive.passes.pass_config import PassConfigParam
from olive.resource_path import OLIVE_RESOURCE_ANNOTATIONS
from olive.strategy.search_parameter import Boolean, Categorical, Conditional

logger = logging.getLogger(__name__)

_inc_quantization_config = {
    "device": PassConfigParam(
        type_=str,
        default_value="cpu",
        description="""
            Intel® Neural Compressor quantization device. Support 'cpu' and 'gpu'.
        """,
    ),
    "backend": PassConfigParam(
        type_=str,
        default_value="default",
        description="""
            Backend for model execution. Support 'default', 'onnxrt_trt_ep', 'onnxrt_cuda_ep'
        """,
    ),
    "domain": PassConfigParam(
        type_=str,
        default_value="auto",
        description="""
            Model domain. Support 'auto', 'cv', 'object_detection', 'nlp' and 'recommendation_system'.
            Intel® Neural Compressor Adaptor will use specific quantization settings for different domains
            automatically, and explicitly specified quantization settings will override the automatic setting.
            If users set domain as auto, automatic detection for domain will be executed.
        """,
    ),
    "recipes": PassConfigParam(
        type_=dict,
        default_value={},
        description="""
            Recipes for Intel® Neural Compressor quantization, support list is as below.
                'smooth_quant': whether do smooth quant
                'smooth_quant_args': parameters for smooth_quant
                'fast_bias_correction': whether do fast bias correction
                'weight_correction': whether do weight correction
                'gemm_to_matmul': whether convert gemm to matmul and add, only valid for onnx models
                'graph_optimization_level': support 'DISABLE_ALL', 'ENABLE_BASIC', 'ENABLE_EXTENDED', 'ENABLE_ALL'
                                        only valid for onnx models
                'first_conv_or_matmul_quantization': whether quantize the first conv or matmul
                'last_conv_or_matmul_quantization': whether quantize the last conv or matmul
                'pre_post_process_quantization': whether quantize the ops in preprocessing and postprocessing
                'add_qdq_pair_to_weight': whether add QDQ pair for weights, only valid for onnxrt_trt_ep
                'optypes_to_exclude_output_quant': don't quantize output of specified optypes
                'dedicated_qdq_pair': whether dedicate QDQ pair, only valid for onnxrt_trt_ep
        """,
    ),
    "reduce_range": PassConfigParam(
        type_=bool,
        default_value=False,
        searchable_values=Boolean(),
        description="""
            Whether use 7 bit to quantization.
        """,
    ),
    "quant_level": PassConfigParam(
        type_=str,
        default_value="auto",
        description="""
            Intel® Neural Compressor allows users to choose different tuning processes by specifying
            the quantization level (quant_level). Currently 3 quant_levels are supported.
            0 is conservative strategy, 1 is basic or user-specified strategy,
            auto (default) is the combination of 0 and 1.
            Please refer to
            https://github.com/intel/neural-compressor/blob/master/docs/source/tuning_strategies.md#tuning-process
            https://github.com/intel/neural-compressor/blob/master/docs/source/tuning_strategies.md#tuning-algorithms
            for more details
        """,
    ),
    "excluded_precisions": PassConfigParam(
        type_=list,
        default_value=[],
        description="""
            Precisions to be excluded, Default value is empty list.
            Intel® Neural Compressor enable the mixed precision with
            fp32 + bf16(only when device is 'gpu' and backend is 'onnxrt_cuda_ep') + int8 by default.
            If you want to disable bf16 data type, you can specify excluded_precisions = ['bf16'].
        """,
    ),
    "tuning_criterion": PassConfigParam(
        type_=dict,
        default_value={},
        description="""
            Instance of TuningCriterion class. In this class you can set strategy, strategy_kwargs,
            timeout, max_trials and objective.
        """,
    ),
    "metric": PassConfigParam(
        type_=Optional[Metric],
        default_value=None,
        description="""
            Accuracy metric to generate an evaluation function for Intel® Neural Compressor
            accuracy aware tuning.
        """,
    ),
}

_inc_static_dataloader_config = {
    "data_dir": PassConfigParam(
        type_=OLIVE_RESOURCE_ANNOTATIONS,
        is_path=True,
        description="""
            Path to the directory containing the dataset.
            For local data, it is required if approach is 'static'.
        """,
    ),
    "batch_size": PassConfigParam(
        type_=int,
        default_value=1,
        description="""
            Batch size for calibration, required if approach is 'static'.
        """,
    ),
    "dataloader_func": PassConfigParam(
        type_=Union[Callable, str],
        required=True,
        is_object=True,
        description="""
            Function/function name to generate dataloader for calibration,
            required if approach is 'static'
        """,
    ),
}

_inc_static_optional_config = {
    "quant_format": PassConfigParam(
        type_=str,
        default_value="QOperator",
        searchable_values=Categorical(["QOperator", "QDQ"]),
        description="""
            Quantization format. Support 'QDQ' and 'QOperator'.
        """,
    ),
    "calibration_sampling_size": PassConfigParam(
        type_=Union[list, int],
        default_value=[100],
        description="""
            Number of calibration sample.
        """,
    ),
}

_inc_tuning_criterion_config = {
    "strategy": PassConfigParam(
        type_=str,
        default_value="basic",
        description="""
            Strategy name used in tuning. Details in
            https://github.com/intel/neural-compressor/blob/master/docs/source/tuning_strategies.md#basic
        """,
    ),
    "strategy_kwargs": PassConfigParam(
        type_=dict,
        default_value=None,
        description="""
            Parameters for strategy.
        """,
    ),
    "timeout": PassConfigParam(
        type_=int,
        default_value=0,
        description="""
            Tuning timeout (seconds). Default value is 0 which means early stop.
        """,
    ),
    "max_trials": PassConfigParam(
        type_=int,
        default_value=5,
        description="""
            Max tune times. Default value is 5. Combine with timeout field to decide when to exit.
        """,
    ),
    "objective": PassConfigParam(
        type_=str,
        default_value="performance",
        description="""
            String or dict. Objective with accuracy constraint guaranteed. String value supports
            'performance', 'modelsize', 'footprint'. Default value is 'performance'.
        """,
    ),
}


class IncQuantization(Pass):
    """
    Quantize ONNX model with Intel® Neural Compressor.
    """

    _requires_user_script = True
    _requires_data_config = True

    def _initialize(self):
        super()._initialize()

    @staticmethod
    def _default_config(accelerator_spec: AcceleratorSpec) -> Dict[str, PassConfigParam]:
        config = {
            "approach": PassConfigParam(
                type_=str,
                default_value="static",
                searchable_values=Categorical(["dynamic", "static"]),
                description="""
                Intel® Neural Compressor Quantization mode. 'dynamic' for dynamic quantization,
                'static' for static quantization.
            """,
            )
        }

        # common quantization config
        config.update(deepcopy(_inc_quantization_config))

        # tuning criterion config
        for key, value in deepcopy(_inc_tuning_criterion_config).items():
            config["tuning_criterion"].default_value.update({key: value.default_value})

        # static quantization config
        config.update(deepcopy(_inc_static_dataloader_config))
        inc_static_optional_config = deepcopy(_inc_static_optional_config)
        for _, value in inc_static_optional_config.items():
            # default value of quant_format is conditional on approach
            if isinstance(value.searchable_values, Categorical):
                # ignore the parameter quant_format if approach is dynamic, if approach is static,
                # use the searchable_values in inc_static_optional_config by making it conditional
                value.searchable_values = Conditional(
                    parents=("approach",),
                    support={("static",): value.searchable_values},
                    default=Categorical(["default"]),
                )
            elif isinstance(value.searchable_values, Conditional):
                # ignore the parameter quant_format if approach is dynamic, if approach is static,
                # use the searchable_values in inc_static_optional_config by expanding the parents
                value.searchable_values = Conditional(
                    parents=("approach",) + value.searchable_values.parents,
                    support={
                        ("static",) + key: value.searchable_values.support[key]
                        for key in value.searchable_values.support
                    },
                    default=Categorical(["default"]),
                )
        config.update(inc_static_optional_config)

        # external data config
        config.update(get_external_data_config())
        return config

    def _set_eval_func(self, accuracy_metric, sub_type):
        # set eval_func for INC according to Olive accuracy metric
        def eval_func(model):
            # eval_func for Intel® Neural Compressor quantization take model as input,
            # and return evaluation value.

            # temporarily save model as onnx model
            tmp_dir = tempfile.TemporaryDirectory(prefix="olive_tmp")
            tmp_model_path = Path(tmp_dir.name) / "tmp_model.onnx"

            # save as olive onnx model
            # TODO: investigate why save_as_external_data = True is not working
            # it cannot find the external data file
            olive_model = model_proto_to_olive_model(
                model,
                tmp_model_path,
                {
                    "save_as_external_data": False,
                    "all_tensors_to_one_file": True,
                    "external_data_name": None,
                },
            )

            # create evaluator for model
            evaluator = OliveEvaluatorFactory.create_evaluator_for_model(olive_model)

            # evaluate model
            result = evaluator.evaluate(
                olive_model,
                [accuracy_metric],
                self._accelerator_spec.accelerator_type,
                [self._accelerator_spec.execution_provider],
            )
            joint_key = joint_metric_key(accuracy_metric.name, sub_type.name)
            return result[joint_key].value

        return eval_func

    def _set_accuracy_criterion(self, sub_type):
        # set accuracy criterion for INC according to Olive accuracy metric goal
        goal_type = sub_type.goal.type
        goal_value = sub_type.goal.value
        higher_is_better = sub_type.higher_is_better

        if goal_type == "max-degradation":
            tolerable_loss = goal_value
            criterion = "absolute"
        elif goal_type == "min-improvement":
            tolerable_loss = -goal_value
            criterion = "absolute"
        elif goal_type == "percent-max-degradation":
            tolerable_loss = goal_value / 100
            criterion = "relative"
        elif goal_type == "percent-min-improvement":
            tolerable_loss = -goal_value / 100
            criterion = "relative"
        else:
            assert False, (
                "Accuracy metric goal type for Intel® Neural Compressor quantization only suuport "
                "'max-degradation', 'min-improvement', 'percent-max-degradation' and 'percent-min-improvement'."
            )

        return higher_is_better, criterion, tolerable_loss

    def _set_tuning_config(self, run_config):
        # set criterion and eval func for INC
        # INC quantization without accuracy aware tuning situation:
        #  1. 'metric' is not set
        #  2. 'metric' is set, but it is not an accuracy metric
        #  3. 'metric' is set, and it is an accuracy metric, but 'goal' is not set in the 'metric'
        # INC quantization with accuracy aware tuning situation:
        #  1. 'metric' is set, and it is an accuracy metric, and 'goal' is set in the 'metric'
        try:
            from neural_compressor.config import AccuracyCriterion, TuningCriterion
        except ImportError:
            raise ImportError(
                "Please install `olive-ai[inc]` or `neural-compressor` to use Intel® Neural Compressor quantization"
            )

        _inc_quantization_config = deepcopy(run_config)

        accuracy_criterion = AccuracyCriterion()
        tuning_criterion = TuningCriterion()
        eval_func = None
        accuracy_metric = None

        if _inc_quantization_config["metric"] is not None and len(_inc_quantization_config["metric"]) != 0:
            accuracy_metric = Metric(**_inc_quantization_config["metric"])
            logger.warning(
                "'metric' is set in INC Quantization Pass. Please make sure it is an accuracy metric, "
                "and then Intel® Neural Compressor will quantize model with accuracy aware tuning."
            )
        else:
            logger.warning(
                "'metric' is not set for INC Quantization Pass. "
                "Intel® Neural Compressor will quantize model without accuracy aware tuning. "
                "Please set 'metric' if you want to use Intel® Neural Compressor"
                "quantization with accuracy aware tuning."
            )

        if accuracy_metric is not None:
            assert hasattr(accuracy_metric, "sub_types"), "There is no sub_type in Accuracy metric."
            sub_type = None
            if len(accuracy_metric.sub_types) != 0:
                sub_type = accuracy_metric.sub_types[0]
            if sub_type is not None and sub_type.goal is not None:
                eval_func = self._set_eval_func(accuracy_metric, sub_type)

                higher_is_better, criterion, tolerable_loss = self._set_accuracy_criterion(sub_type)
                accuracy_criterion = AccuracyCriterion(
                    higher_is_better=higher_is_better, criterion=criterion, tolerable_loss=tolerable_loss
                )

                tuning_criterion = TuningCriterion(**_inc_quantization_config["tuning_criterion"])
            else:
                logger.warning(
                    "'goal' is not set in 'metric'. "
                    "Intel® Neural Compressor will quantize model without accuracy aware tuning. "
                    "Please set 'goal' in 'metric' if you want to use "
                    "Intel® Neural Compressor quantization with accuracy aware tuning."
                )

        return eval_func, accuracy_criterion, tuning_criterion

    def _run_for_config(self, model: ONNXModel, config: Dict[str, Any], output_model_path: str) -> ONNXModel:
        try:
            from neural_compressor import quantization
            from neural_compressor.config import PostTrainingQuantConfig
        except ImportError:
            raise ImportError(
                "Please install `olive-ai[inc]` or `neural-compressor` to use Intel® Neural Compressor quantization"
            )

        # start with a copy of the config
        run_config = deepcopy(config)
        is_static = run_config["approach"] == "static"

        output_model_path = ONNXModel.resolve_path(output_model_path)

        eval_func, accuracy_criterion, tuning_criterion = self._set_tuning_config(run_config)

        # keys not needed for quantization
        to_delete = [
            "script_dir",
            "user_script",
            "data_dir",
            "batch_size",
            "dataloader_func",
            "tuning_criterion",
            "data_config",
            "metric",
        ]
        to_delete += list(get_external_data_config().keys())
        for key in to_delete:
            if key in run_config:
                del run_config[key]

        ptq_config = PostTrainingQuantConfig(
            **run_config, accuracy_criterion=accuracy_criterion, tuning_criterion=tuning_criterion
        )

        inc_calib_dataloader = None
        if is_static:
            if self._user_module_loader:
                inc_calib_dataloader = self._user_module_loader.call_object(
                    self._fixed_params["dataloader_func"],
                    get_local_path(self._fixed_params["data_dir"]),
                    self._fixed_params["batch_size"],
                )
            elif self._data_config:
                inc_calib_dataloader = self._data_config.to_data_container().create_calibration_dataloader()

        q_model = quantization.fit(
            model.model_path, ptq_config, calib_dataloader=inc_calib_dataloader, eval_func=eval_func
        )
        if eval_func is not None and q_model is None:
            logger.error(
                "Intel® Neural Compressor quantization does not "
                "find any quantized model which meet accuracy goal. "
                "Try to increase 'max_trials' in 'tuning_criterion'."
            )
        # save the model to the output path and return the model
        return model_proto_to_olive_model(q_model.model, output_model_path, config)


class IncDynamicQuantization(IncQuantization):
    """Intel® Neural Compressor Dynamic Quantization Pass"""

    _requires_user_script = True

    @staticmethod
    def _default_config(accelerator_spec: AcceleratorSpec) -> Dict[str, Any]:
        config = {
            "approach": PassConfigParam(type_=str, default_value="dynamic", description="dynamic quantization mode")
        }
        # common quantization config
        config.update(deepcopy(_inc_quantization_config))

        # tuning criterion config
        for key, value in deepcopy(_inc_tuning_criterion_config).items():
            config["tuning_criterion"].default_value.update({key: value.default_value})

        # external data config
        config.update(get_external_data_config())
        return config


class IncStaticQuantization(IncQuantization):
    """Intel® Neural Compressor Static Quantization Pass"""

    _requires_user_script = True

    @staticmethod
    def _default_config(accelerator_spec: AcceleratorSpec) -> Dict[str, Any]:
        config = {
            "approach": PassConfigParam(type_=str, default_value="static", description="static quantization mode")
        }
        # common quantization config
        config.update(deepcopy(_inc_quantization_config))

        # tuning criterion config
        for key, value in deepcopy(_inc_tuning_criterion_config).items():
            config["tuning_criterion"].default_value.update({key: value.default_value})

        # static quantization specific config
        config.update(deepcopy(_inc_static_dataloader_config))
        config.update(deepcopy(_inc_static_optional_config))
        # external data config
        config.update(get_external_data_config())
        return config
