"""Check azureml component and pipeline definitions.

Find all pipeline and component yaml files and then
- check whether component inputs/outputs match the inputs/outputs used in the command
- check whether inputs of pipeline jobs exist in pipeline inputs or outputs of earlier components
- check whether inputs/outputs of pipeline jobs match with component inputs/outputs

Will print error for each missing or unused element and stop with exit code 1 if there is an error.

You can run this file on your own or include it as pre-commit hook (python must be available in the environment).
Next steps:
- function validate_pipeline_component_match is too complex, spit in separate functions
- improve handling of optional inputs?
- unittests!!
"""
# ruff: noqa: INP001, C901

import ast
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv
from fire import Fire

# Set up logging, change level when debugging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(name=__name__)

# Load .env
dotenv = find_dotenv(raise_error_if_not_found=False)
load_dotenv(dotenv, override=True)


def detect_aml_type(data: dict[str, str]) -> str:
    """Detects the AML type based on the schema URL.

    Args:
        data: A dictionary containing the schema information.

    Returns:
        The detected AML type, which can be "pipelineJob", "component", "environment",
        or "unsupported" if the schema is not recognized.
    """
    schema = data["$schema"]
    if schema.endswith("/pipelineJob.schema.json"):
        return "pipelineJob"
    if schema.endswith("/commandComponent.schema.json"):
        return "commandComponent"
    if schema.endswith("/parallelComponent.schema.json"):
        return "parallelComponent"

    return "unsupported"


def read_yaml(filepath: Path) -> dict[str, Any]:
    """Read and parse a YAML file, validating Azure ML schema compliance.

    Args:
        filepath: The path to the YAML file to read.

    Returns:
        The parsed YAML data as a dictionary.
        The returned dictionary includes additional metadata fields:
        - "_filepath": The original file path
        - "_amltype": The detected Azure ML type
        - "_redeployed": Boolean flag indicating redeployment status

    Raises:
        ValueError: If the file does not have a name or $schema attribute required for Azure ML files.
    """
    with open(filepath, "r") as file:
        data = yaml.safe_load(file)
    # return nothing if it is not an azureml file
    if "$schema" not in data or not data.get("$schema").startswith(
        "https://azuremlschemas.azureedge.net"
    ):
        msg = f"File {file} has no aml $schema"
        raise ValueError(msg)
    # check if it contains a name, necessary for linking files
    if "name" not in data:
        msg = f"Please give {filepath!s} a name attribute"
        raise KeyError(msg)
    # add filepath and filetype properties
    data["_filepath"] = filepath
    data["_amltype"] = detect_aml_type(data=data)
    return data


def get_function_arguments(
    file_path: Path, function_name: str
) -> tuple[list[str], list[str]]:
    """Read a Python file and extract argument information for a specific function.

    Args:
        file_path (Path): Path to the Python file
        function_name (str): Name of the function to analyze

    Returns:
        tuple: (all_args, required_args) - Lists of all arguments and required arguments
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    tree = ast.parse(content)

    # Find the function definition
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            func_node = node
            break

    if not func_node:
        msg = f"Function '{function_name}' not found in {file_path}"
        raise ValueError(msg)

    # Extract positional only and regular arguments information
    # see https://peps.python.org/pep-0570/ for more information on argument types
    # (we probably only use regular arguments)
    pos_only_args = [arg.arg for arg in func_node.args.posonlyargs]
    regular_args = [arg.arg for arg in func_node.args.args]
    all_args = pos_only_args + regular_args
    # of pos only and regular arguments, you cannot place args without default after first arg with a default
    n_defaults = len(func_node.args.defaults)
    required_args = all_args[:-n_defaults]

    # Handle keyword-only arguments (see link above for more info)
    kw_only_args = [arg.arg for arg in func_node.args.kwonlyargs]
    req_kw_only_args = []
    for i, kw_only_arg in enumerate(kw_only_args):
        # kw_defaults is a list same length as kwonlyargs, with an object when it has a default, None otherwise
        if func_node.args.kw_defaults[i] is None:
            req_kw_only_args.append(kw_only_arg)
    all_args += kw_only_args
    required_args += req_kw_only_args

    # Handle *args and **kwargs
    if func_node.args.vararg:
        all_args.append(f"*{func_node.args.vararg.arg}")
    if func_node.args.kwarg:
        all_args.append(f"**{func_node.args.kwarg.arg}")

    return all_args, required_args


def find_module_path(
    module_name: str, wd: Path, packages: dict[str, str] | None
) -> Path:
    # explicit .py files (usually a .py file within the component directory)
    if ".py" in module_name:
        # referencing a path, should be found relative to component
        module_path = wd.joinpath(module_name).resolve()
        if not module_path.exists():
            msg = f"Module {module_name} refers to {module_path} but does not exist"
            raise ValueError(msg)
        return module_path

    # module refers to a python package
    package_name = module_name.split(".")[0]
    alt_path = None
    if packages is not None and package_name in packages:
        # path set in cli argument
        alt_path = Path(packages[package_name])
        source = "args"
    else:
        env_var_name = f"AMLPC_{package_name.upper()}"
        env_path = os.environ.get(env_var_name)
        if env_path is not None:
            # path set in environment variable
            alt_path = Path(env_path)
            source = "env"
    if alt_path is not None:
        # path was found in cli arguments or environment variable
        # create a path from module by replacing dots with slashes and add .py extension
        module_dir = module_name.replace(".", "/") + ".py"
        # assume src-layout of package (as used in our cookiecutter)
        rel_path = Path(alt_path) / "src" / module_dir
        # resolve relative path to working directory
        module_path = Path.cwd().joinpath(rel_path).resolve()
        if not module_path.exists():
            msg = f"Module path of {package_name} is set to '{alt_path}' in {source} but cannot be found."
            raise ValueError(msg)
        return module_path

    # finally, try finding package in active python environment
    try:
        spec = importlib.util.find_spec(module_name)
        module_path = Path(spec.origin)
    except ModuleNotFoundError:
        msg = f"Module {package_name} cannot be found in active python environment. Add to environment or add alternative path via cli argument or environment variable."
        raise ValueError(msg)


def check_command_arguments(
    command: str, wd: Path, packages: dict[str, str] | None
) -> list[str]:
    """Check command arguments for validity.

    Supports only python -m fire commands on modules (not local .py files).

    Args:
        command: The command string to validate.
        wd: working directory of command
        packages: dict with packag nams as keys and locations as values

    Returns:
        A list of error messages. Empty list if no errors.
    """
    errors: list[str] = []
    if "python -m fire" not in command:
        # only python fire commands supported for now
        return errors

    # list arguments
    passed_arguments = re.findall(r"--([a-zA-Z0-9_]+)", command)

    # find module and function name
    pattern = r"fire\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*)\s+([a-zA-Z0-9_]+)"
    match = re.search(pattern, command)
    if not match:
        msg: str = "Could not identify module and function from command"
        errors.append(msg)
        return errors
    module_name = match.group(1)
    function_name = match.group(2)

    module_path = find_module_path(module_name=module_name, wd=wd, packages=packages)

    # Get function and inspect arguments
    all_arguments, required_arguments = get_function_arguments(
        file_path=module_path, function_name=function_name
    )

    # Check if all passed arguments are defined
    unknown_arguments: set[str] = set(passed_arguments) - set(all_arguments)

    # Check for missing required arguments
    missing_arguments: set[str] = set(required_arguments) - set(passed_arguments)

    if unknown_arguments and "**kwargs" not in all_arguments:
        # when using kwargs in function, we cannot detect unknown arguments..
        errors.append(f"Unknown arguments used in command: {unknown_arguments}")

    if missing_arguments:
        errors.append(f"Missing required arguments in command: {missing_arguments}")

    return errors


def validate_component_command(
    data: dict, packages: dict[str, str] | None, disable_function_check: bool
) -> list[str]:
    """Validates that all inputs in a component command exist in the inputs section.

    Args:
        data: dict with yaml contents
        packages: dict with packag nams as keys and locations as values
        disable_function_check: disable checks on consistency of component command and function arguments

    Returns:
        List of errors
    """
    # Report all errors
    errors: list[str] = []

    # extra check whether schema is really component
    if "Component" not in data.get("$schema", ""):
        return errors

    # Get inputs and outputs definitions
    inputs_def: dict = data.get("inputs", {})
    outputs_def: dict = data.get("outputs", {})

    # Get the command
    if data["_amltype"] == "commandComponent":
        command: str = data.get("command", "")
    elif data["_amltype"] == "parallelComponent":
        command: str = data["task"].get("program_arguments", "")
    else:
        msg = f"Component of type {data['_amltype']} is not implemented yet"
        raise NotImplementedError(msg)

    # Extract all variables from the command using regex
    # This pattern matches ${{inputs.variable}} and ${{outputs.variable}} format
    def extract_vars(string: str, io_type: str) -> set[str]:
        return set(
            re.findall(
                pattern=r"\$\{\{\s*io\.(\w+)\s*\}\}".replace("io", io_type),
                string=string,
            )
        )

    input_vars = extract_vars(command, "inputs")
    if data["_amltype"] == "parallelComponent":
        # add additional input block by merging sets
        input_vars = input_vars | extract_vars(data.get("input_data", ""), "inputs")
    output_vars = extract_vars(command, "outputs")

    # Check if all input variables are defined
    missing_inputs: set[str] = input_vars - set(inputs_def.keys())
    # Check if all output variables are defined
    missing_outputs: set[str] = output_vars - set(outputs_def.keys())

    # Check for unused inputs (defined but not used)
    unused_inputs: set[str] = set(inputs_def.keys()) - input_vars
    # Check for unused outputs (defined but not used)
    unused_outputs: set[str] = set(outputs_def.keys()) - output_vars

    if missing_inputs:
        errors.append(f"Missing input definitions: {missing_inputs}")

    if missing_outputs:
        errors.append(f"Missing output definitions: {missing_outputs}")

    if unused_inputs:
        errors.append(f"Unused input definitions: {unused_inputs}")

    if unused_outputs:
        errors.append(f"Unused output definitions: {unused_outputs}")

    if not disable_function_check:
        errors += check_command_arguments(
            command=command, wd=data["_filepath"].parent, packages=packages
        )

    # Return True if no errors, False otherwise
    return errors


def validate_pipeline_inputs(data: dict) -> list[str]:
    """Validates that all job inputs in a pipeline YAML file exist.

    Args:
        data: dict with yaml contents

    Returns:
        List of errors
    """
    errors: list[str] = []

    # extra check whether this is a pipeline schema
    if "pipeline" not in data.get("$schema", ""):
        return errors

    # Extract all input names from the inputs section
    inputs: set[str] = set()
    for key in data.get("inputs", {}):
        inputs.add(f"parent.inputs.{key}")
    unused_inputs: set[str] = inputs.copy()

    # Check each job's inputs
    for job_name, job_config in data.get("jobs", {}).items():
        for input_name in job_config.get("inputs", {}):
            input_value: str = job_config["inputs"][input_name]
            # Extract the input name from the reference, if it is a reference to main inputs block from yaml file
            if isinstance(input_value, str) and "$" in input_value:
                # ref is something like '${{reference}}', but may contain spaces everywhere
                ref_input: str = input_value.lstrip("$").lstrip("{").rstrip("}").strip()
                if ref_input not in inputs:
                    errors.append(
                        f"Job '{job_name}' input references non-existent input '{ref_input}'"
                    )
                unused_inputs.discard(ref_input)
        for output_name, output in job_config.get("outputs", {}).items():
            # can be a string of a path or a dictionary containing the path
            output_path = output.get("path", "") if isinstance(output, dict) else output
            # add outputs as possible input for next jobs
            inputs.add(f"parent.jobs.{job_name}.outputs.{output_name}")
            # inputs defined in pipeline could be used in output path definition
            used_inputs = set(
                re.findall(
                    pattern=r"\$\{\{\s*(parent\.inputs\.\w+)\s*\}\}", string=output_path
                )
            )
            errors.extend(
                [
                    f"Job '{job_name}' output references non-existent input '{ref_input}'"
                    for ref_input in used_inputs
                    if ref_input not in inputs
                ]
            )
            unused_inputs: set[str] = unused_inputs - used_inputs

    # Check for unused inputs
    errors.extend(
        f"Input '{unused_input}' is defined but not used"
        for unused_input in unused_inputs
    )

    return errors


def validate_pipeline_component_match(
    pipelinejobs: dict, components: dict[str, Any]
) -> dict[Path, list[str]]:
    """Validates that component inputs/outputs match pipeline job inputs/outputs.

    Args:
        pipelinejobs: dict with name name of pipelinejob and yaml contents
        components: dict with name name of components and yaml contents

    Returns:
        True if all matches, False otherwise
    """
    all_errors: dict[Path, list[str]] = {}
    for data in pipelinejobs.values():
        errors: list[str] = []
        warnings: list[str] = []
        # extra check whether schema is really component
        if "pipeline" not in data.get("$schema", ""):
            continue

        for job_name, job_config in data.get("jobs", {}).items():
            # check if this job is based on a component
            if "component" not in job_config:
                continue
            # assumes component has form 'azureml:component_name@latest' or 'azureml:component_name:123'
            component_name = (
                job_config["component"].split(":")[1].rsplit("@")[0].rsplit(":")[0]
            )
            if component_name not in components:
                # component is defined in another repo?
                warnings.append(
                    f"Component '{component_name}' is used but not found in this repo, cannot validate."
                )
                continue
            for io_type in ["inputs", "outputs"]:
                # for inputs and outputs, check if those in job definition match those in component definition
                # retrieve unique component input/output names without error if there are no inputs/outputs
                component_io: set[str] = set(
                    components[component_name].get(io_type, {}).keys()
                )
                job_io: set[str] = set(job_config.get(io_type, {}).keys())
                if job_only := job_io - component_io:
                    errors.append(
                        f"Job '{job_name}' defines {io_type} unused in component '{component_name}': {job_only}"
                    )
                if comp_only := component_io - job_io:
                    errors.append(
                        f"Job '{job_name}' references {io_type} missing in component '{component_name}': {comp_only}"
                    )

        # Print all warnings
        if warnings:
            logger.warning("Warnings in %s:", data["_filepath"])
            for warning in warnings:
                logger.warning("  %s", warning)
        if errors:
            all_errors[data["_filepath"]] = errors

    return all_errors


def check_aml(
    packages: dict[str, str] | None = None, disable_function_check: bool = False
) -> None:
    """Main function to validate Azure ML components and pipelines.

    This function scans for component and pipeline YAML files, validates them
    using specific validation functions, and reports any errors found.

    The validation includes:
    - Component command validation
    - Pipeline input validation
    - Pipeline-component crossvalidation

    Args:
        packages: dict with package names as keys and locations as values
        disable_function_check: disable checks on consistency of component command and function arguments

    Returns:
        None: Exits with status code 1 if any validation errors are found,
              otherwise exits with status code 0.
    """
    # find all component and pipeline files
    components: dict[str, dict] = {}
    pipelinejobs: dict[str, dict] = {}

    # Index all aml yaml files
    logger.info("Finding all AzureML yaml files")
    all_yml_files: list[Path] = []
    # use os.walk, is much faster than .glob (especially when skipping hidden directories)
    for root, dirs, files in os.walk("."):
        # Skip hidden directories, like .git, to speed up a LOT
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        # append all yaml files
        all_yml_files.extend(
            [Path(root) / file for file in files if file.endswith(".yml")]
        )
    logger.info("Reading all AzureML yaml files")
    for filepath in all_yml_files:
        try:
            data = read_yaml(filepath)
        except ValueError:
            # file is not an aml file, do not use
            continue
        if "Component" in data["_amltype"]:
            components[data["name"]] = data
        elif data["_amltype"] == "pipelineJob":
            pipelinejobs[data["name"]] = data

    logger.info("Checking component commands")
    all_errors: dict[Path, list[str]] = {}
    for data in components.values():
        if comp_errors := validate_component_command(
            data=data, packages=packages, disable_function_check=disable_function_check
        ):
            all_errors[data["_filepath"]] = comp_errors

    logger.info("Checking pipeline inputs/outputs")
    for data in pipelinejobs.values():
        if pipe_errors := validate_pipeline_inputs(data=data):
            all_errors[data["_filepath"]] = pipe_errors

    logger.info("Checking pipeline i/o with component i/o")
    combi_errors: dict[Path, list[str]] = validate_pipeline_component_match(
        pipelinejobs=pipelinejobs, components=components
    )
    for path, errors in combi_errors.items():
        all_errors[path].extend(errors)

    # Print all errors
    for path, errors in all_errors.items():
        logger.error("Errors in %s:", path)
        for error in errors:
            logger.error("  %s", error)

    sys.exit(1 if all_errors else 0)


def main():
    Fire(check_aml)


if __name__ == "__main__":
    main()
