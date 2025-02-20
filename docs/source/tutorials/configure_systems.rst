.. _configuring_olivesystem:

Configuring OliveSystem
=========================

This document describes how to configure the different types of OliveSystems

Local System
-------------

.. tabs::
    .. tab:: Config JSON

        .. code-block:: json

            {
                "type": "LocalSystem",
                "config": {
                    "accelerators": ["cpu"]
                }
            }

    .. tab:: Python Class

        .. code-block:: python

            from olive.systems.local import LocalSystem
            from olive.system.common import Device

            local_system = LocalSystem(
                accelerators=[Device.CPU]
            )

Please refer to :ref:`local_system_config` for more details on the config options.

AzureML System
---------------

Prerequisites
^^^^^^^^^^^^^

1. azureml extra dependencies installed.

    .. code-block:: bash

        pip install olive-ai[azureml]

    or

    .. code-block:: bash

        pip install azure-ai-ml azure-identity

2. AzureML Workspace with necessary compute created. Refer to
`this <https://learn.microsoft.com/en-us/azure/machine-learning/concept-workspace>`_ for more details. Download
the workspace config json.

System Configuration
^^^^^^^^^^^^^^^^^^^^^

.. tabs::
    .. tab:: Config JSON

        .. code-block:: json

            {
                "type": "AzureML",
                "config": {
                    "aml_compute": "cpu-cluster",
                    "aml_docker_config": {
                        "base_image": "mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu20.04",
                        "conda_file_path": "conda.yaml"
                    }
                }
            }

    .. tab:: Python Class

        .. code-block:: python

            from olive.systems.azureml import AzureMLDockerConfig, AzureMLSystem

            docker_config = AzureMLDockerConfig(
                base_image="mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu20.04",
                conda_file_path="conda.yaml",
            )
            aml_system = AzureMLSystem(
                aml_compute="cpu-cluster",
                aml_docker_config={
                        "base_image": "mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu20.04",
                        "conda_file_path": "conda.yaml"
                    }
            )

Please refer to this `example <https://github.com/microsoft/Olive/blob/main/examples/bert/conda.yaml>`__
for :code:`"conda.yaml"`.

.. important::

    The AzureML environment must have :code:`olive-ai` installed!

Please refer to :ref:`azureml_system_config` for more details on the config options.

AzureML Readymade Systems
^^^^^^^^^^^^^^^^^^^^^^^^^

There are some readymade systems available for AzureML. These systems are pre-configured with the necessary.
    .. code-block:: json

            {
                "type": "AzureNDV2System",
                "config": {
                    "aml_compute": "gpu-cluster",
                    "aml_docker_config": {
                        "base_image": "mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu20.04",
                        "conda_file_path": "conda.yaml"
                    }
                }
            }

Please refer to :ref:`system_alias` for the list of supported AzureML readymade systems.


Docker System
--------------

Prerequisites
^^^^^^^^^^^^^

1. Docker Engine installed on the host machine.

2. docker extra dependencies installed.

    .. code-block:: bash

        pip install olive-ai[docker]

    or

    .. code-block:: bash

        pip install docker

System Configuration
^^^^^^^^^^^^^^^^^^^^^

.. tabs::
    .. tab:: Config JSON

        .. code-block:: json

            {
                "type": "Docker",
                "config": {
                    "local_docker_config": {
                        "image_name": "olive",
                        "build_context_path": "docker",
                        "dockerfile": "Dockerfile"
                    }
                }
            }

    .. tab:: Python Class

        .. code-block:: python

            from olive.systems.docker import DockerSystem, LocalDockerConfig

            local_docker_config = LocalDockerConfig(
                image_name="olive",
                build_context_path="docker",
                dockerfile="Dockerfile",
            )
            docker_system = DockerSystem(local_docker_config=local_docker_config)

Please refer to this `example <https://github.com/microsoft/Olive/tree/main/examples/bert/docker>`__
for :code:`"docker"` and :code:`"Dockerfile"`.

.. important::

    The docker container must have :code:`olive-ai` installed!

Please refer to :ref:`docker_system_config` for more details on the config options.

Python Environment System
--------------------------

.. tabs::
    .. tab:: Config JSON

        .. code-block:: json

            {
                "type": "PythonEnvironment",
                "config": {
                    "python_environment_path": "/home/user/.virtualenvs/myenv",
                    "accelerators": ["cpu"]
                }
            }

    .. tab:: Python Class

        .. code-block:: python

            from olive.systems.python_environment import PythonEnvironmentSystem
            from olive.system.common import Device

            python_environment_system = PythonEnvironmentSystem(
                python_environment_path = "/home/user/.virtualenvs/myenv",
                device = Device.CPU
            )

.. important::

    The python environment system can only be used to evaluate onnx models. It must have :code:`onnxruntime` installed!

Please refer to :ref:`python_environment_system_config` for more details on the config options.
