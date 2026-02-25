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
        name: check azureml pipeline and component inputs/outputs consistency
        pass_filenames: false
```

### External packages

When a component command refers to functions of external packages, you must do an additional setup to check this. Use one of the following options.

The order in which the script searches for the correct python file:

1. If it ends with a .py extension, it is assumed this is a local file relative to the component yaml file.
2. The packages pre-commit hook argument is checked and used if passed.
3. If it exists, the environment variable is used.
4. Try finding the module in the active python environment.

See what works for your project and CI/CD setup.
Using submodules + hook argument is probably easiest both locally and in remote CI/CD, but submodules may not work due to github/devops policies.
Alternatively, add the module to the python environment to use in a remote CI/CD and overwrite the path in .env for local development (so you can refer to the development version of the package locally).

#### Pass local path

You can pass local paths for external packages.
Advised is to use [git submodules](https://git-scm.com/book/en/v2/Git-Tools-Submodules), so others using the same repository have the same file structure.
Advise: add within a lib folder.

Then, you can either pass the path via environment variables (for example within a .env file within your repository), such as `AMLPC_{PACKAGE_NAME}=lib/{REPO_NAME}`.

Or, pass as pre-commit hook argument:

```
-   repo: https://github.com/JoeriA/aml-pre-commit
    rev: 0.1.0
    hooks:
    -   id: check-aml
        name: check azureml pipeline and component inputs/outputs consistency
        pass_filenames: false
        args: ["--packages={'{PACKAGE_NAME}': 'lib/{REPO_NAME}'}"]
```

#### Add module to pre-commit environment

You can also add additional dependencies to the pre-commit environment with:

```
-   repo: https://github.com/JoeriA/aml-pre-commit
    rev: 0.1.0
    hooks:
    -   id: check-aml
        name: check azureml pipeline and component inputs/outputs consistency
        pass_filenames: false
        additional_dependencies:
          - {PACKAGE_NAME}==1.2.3
```

You may want to use [prek](https://github.com/j178/prek) so you can use uv to manage dependencies (for example adding [additional index](https://docs.astral.sh/uv/reference/environment/#uv_index), possibly with username/password)

#### Disable
Alternatively, you can disable the function checks with

```
-   repo: https://github.com/JoeriA/aml-pre-commit
    rev: 0.1.0
    hooks:
    -   id: check-aml
        name: check azureml pipeline and component inputs/outputs consistency
        pass_filenames: false
        args: ["--disable_function_check"]
```

#### Background

To check consistency of the component command with a function, we must parse the function to see the function arguments.
So we must have access to the function.
As pre-commit runs in a separate environment, adding the package somehow via pyproject.toml (uv/poetry/pip) is very difficult.
