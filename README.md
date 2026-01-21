# aml_precommit

Pre-commit hook for checking azure machine learning yaml files consistency. Will check the following and give an error if an input or output is missing or unused:

- Pipelinejob 'parent' i/o consistency with pipelinejob job i/o
- Pipelinejob job i/o consistency with component i/o
- Component i/o consistency with component command/task arguments
- Component command/task arguments consistency with python function arguments

## Installation

Add to your .pre-commit-config.yaml like

```
-   repo: https://github.com/JoeriA/aml-pre-commit
    rev: 0.1.0
    hooks:
    -   id: check-aml
        name: check azureml pipeline and component inputs/outputs consistancy
        pass_filenames: false
```

### External packages

When a component command refers to functions of external packages, you must do an additional setup to check this.

1. First add this external package as a [git submodule](https://git-scm.com/book/en/v2/Git-Tools-Submodules). Advise: add within a lib folder.
2. Add a packages argument to the pre-commit hook call:

```
-   repo: https://github.com/JoeriA/aml-pre-commit
    rev: 0.1.0
    hooks:
    -   id: check-aml
        name: check azureml pipeline and component inputs/outputs consistancy
        pass_filenames: false
        args: ["--packages={'PACKAGE_NAME': 'PACKAGE_LOCATION'}"]
```

Alternatively, you can disable the function checks with

```
-   repo: https://github.com/JoeriA/aml-pre-commit
    rev: 0.1.0
    hooks:
    -   id: check-aml
        name: check azureml pipeline and component inputs/outputs consistancy
        pass_filenames: false
        args: ["--disable_function_check"]
```

#### Background

To check consistency of the component command with a function, we must parse the function to see the function arguments.
So we must have access to the function.
As pre-commit runs in a separate environment, adding the package somehow via pyproject.toml (uv/poetry/pip) is very difficult.
