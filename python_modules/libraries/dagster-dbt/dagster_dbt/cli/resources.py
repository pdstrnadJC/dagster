from typing import Any, Iterator, Mapping, Optional, Sequence, Set

import dagster._check as check
from dagster import Field, Permissive, StringSource, resource
from dagster._annotations import public
from dagster._utils.merger import merge_dicts

from ..dbt_resource import DbtResource
from .types import DbtCliOutput
from .utils import (
    DEFAULT_DBT_TARGET_PATH,
    execute_cli,
    execute_cli_stream,
    parse_manifest,
    parse_run_results,
    remove_run_results,
)

DEFAULT_DBT_EXECUTABLE = "dbt"

# The set of dbt cli commands that result in the creation of a run_results.json output file
# https://docs.getdbt.com/reference/artifacts/run-results-json
DBT_RUN_RESULTS_COMMANDS = ["run", "test", "seed", "snapshot", "docs generate", "build"]

# The following config fields correspond to flags that apply to all dbt CLI commands. For details
# on dbt CLI flags, see
# https://github.com/fishtown-analytics/dbt/blob/1f8e29276e910c697588c43f08bc881379fff178/core/dbt/main.py#L260-L329
CLI_COMMON_FLAGS_CONFIG_SCHEMA = {
    "project-dir": Field(
        config=StringSource,
        is_required=False,
        description=(
            "Which directory to look in for the dbt_project.yml file. Default is the current "
            "working directory and its parents."
        ),
        default_value=".",
    ),
    "profiles-dir": Field(
        config=StringSource,
        is_required=False,
        description=(
            "Which directory to look in for the profiles.yml file. Default = $DBT_PROFILES_DIR or "
            "$HOME/.dbt"
        ),
    ),
    "profile": Field(
        config=StringSource,
        is_required=False,
        description="Which profile to load. Overrides setting in dbt_project.yml.",
    ),
    "target": Field(
        config=StringSource,
        is_required=False,
        description="Which target to load for the given profile.",
    ),
    "vars": Field(
        config=Permissive({}),
        is_required=False,
        description=(
            "Supply variables to the project. This argument overrides variables defined in your "
            "dbt_project.yml file. This argument should be a dictionary, eg. "
            "{'my_variable': 'my_value'}"
        ),
    ),
    "bypass-cache": Field(
        config=bool,
        is_required=False,
        description="If set, bypass the adapter-level cache of database state",
        default_value=False,
    ),
}

# The following config fields correspond to options that apply to all CLI solids, but should not be
# formatted as CLI flags.
CLI_COMMON_OPTIONS_CONFIG_SCHEMA = {
    "warn-error": Field(
        config=bool,
        is_required=False,
        description=(
            "If dbt would normally warn, instead raise an exception. Examples include --models "
            "that selects nothing, deprecations, configurations with no associated models, "
            "invalid test configurations, and missing sources/refs in tests."
        ),
        default_value=False,
    ),
    "dbt_executable": Field(
        config=StringSource,
        is_required=False,
        description=f"Path to the dbt executable. Default is {DEFAULT_DBT_EXECUTABLE}",
        default_value=DEFAULT_DBT_EXECUTABLE,
    ),
    "ignore_handled_error": Field(
        config=bool,
        is_required=False,
        description=(
            "When True, will not raise an exception when the dbt CLI returns error code 1. "
            "Default is False."
        ),
        default_value=False,
    ),
    "target-path": Field(
        config=StringSource,
        is_required=False,
        default_value=DEFAULT_DBT_TARGET_PATH,
        description=(
            "The directory path for target if different from the default `target-path` in "
            "your dbt project configuration file."
        ),
    ),
    "docs_url": Field(
        config=StringSource,
        is_required=False,
        description="The url for where dbt docs are being served for this project.",
    ),
    "json_log_format": Field(
        config=bool,
        description=(
            "When True, dbt will invoked with the `--log-format json` flag, allowing "
            "Dagster to parse the log messages and emit simpler log messages to the event log."
        ),
        default_value=True,
    ),
    "capture_logs": Field(
        config=bool,
        description="When True, logs emitted from dbt will be logged to the Dagster event log.",
        default_value=True,
    ),
    "debug": Field(
        config=bool,
        description="When True, dbt will be invoked with the `--debug` flag.",
        default_value=False,
    ),
}


class DbtCliResource(DbtResource):
    """A resource that allows you to execute dbt cli commands.

    For the most up-to-date documentation on the specific parameters available to you for each
    command, check out the dbt docs:

    https://docs.getdbt.com/reference/commands/run

    To use this as a dagster resource, we recommend using
    :func:`dbt_cli_resource <dagster_dbt.dbt_cli_resource>`.
    """

    def __init__(
        self,
        executable: str,
        default_flags: Mapping[str, Any],
        warn_error: bool,
        ignore_handled_error: bool,
        target_path: str,
        logger: Optional[Any] = None,
        docs_url: Optional[str] = None,
        json_log_format: bool = True,
        capture_logs: bool = True,
        debug: bool = False,
    ):
        self._default_flags = default_flags
        self._executable = executable
        self._warn_error = warn_error
        self._ignore_handled_error = ignore_handled_error
        self._target_path = target_path
        self._docs_url = docs_url
        self._json_log_format = json_log_format
        self._capture_logs = capture_logs
        self._debug = debug
        super().__init__(logger)

    @property
    def default_flags(self) -> Mapping[str, Any]:
        """A set of params populated from resource config that are passed as flags to each dbt CLI command.
        """
        return self._format_params(self._default_flags, replace_underscores=True)

    @property
    def strict_flags(self) -> Set[str]:
        """A set of flags that should not be auto-populated from the default flags unless they are
        arguments to the associated function.
        """
        return {"models", "exclude", "select"}

    def _get_flags_dict(self, kwargs) -> Mapping[str, Any]:
        extra_flags = {} if kwargs is None else kwargs

        # remove default flags that are declared as "strict" and not explicitly passed in
        default_flags = {
            k: v
            for k, v in self.default_flags.items()
            if not (k in self.strict_flags and k not in extra_flags)
        }

        return merge_dicts(
            default_flags, self._format_params(extra_flags, replace_underscores=True)
        )

    @public
    def cli(self, command: str, **kwargs) -> DbtCliOutput:
        """Executes a dbt CLI command. Params passed in as keyword arguments will be merged with the
            default flags that were configured on resource initialization (if any) overriding the
            default values if necessary.

        Args:
            command (str): The command you wish to run (e.g. 'run', 'test', 'docs generate', etc.)

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        command = check.str_param(command, "command")
        return execute_cli(
            executable=self._executable,
            command=command,
            flags_dict=self._get_flags_dict(kwargs),
            log=self.logger,
            warn_error=self._warn_error,
            ignore_handled_error=self._ignore_handled_error,
            target_path=self._target_path,
            docs_url=self._docs_url,
            json_log_format=self._json_log_format,
            capture_logs=self._capture_logs,
            debug=self._debug,
        )

    def cli_stream_json(self, command: str, **kwargs) -> Iterator[Mapping[str, Any]]:
        """Executes a dbt CLI command. Params passed in as keyword arguments will be merged with the
            default flags that were configured on resource initialization (if any) overriding the
            default values if necessary.

        Args:
            command (str): The command you wish to run (e.g. 'run', 'test', 'docs generate', etc.)
        """
        check.invariant(self._json_log_format, "Cannot stream JSON if json_log_format is False.")
        for event in execute_cli_stream(
            executable=self._executable,
            command=command,
            flags_dict=self._get_flags_dict(kwargs),
            log=self.logger,
            warn_error=self._warn_error,
            ignore_handled_error=self._ignore_handled_error,
            json_log_format=self._json_log_format,
            capture_logs=self._capture_logs,
            debug=self._debug,
        ):
            if event.parsed_json_line is not None:
                yield event.parsed_json_line

    @public
    def compile(
        self,
        models: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        select: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> DbtCliOutput:
        """Run the ``compile`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            models (List[str], optional): the models to include in compilation.
            exclude (List[str]), optional): the models to exclude from compilation.
            select (List[str], optional): the models to include in compilation.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("compile", models=models, exclude=exclude, select=select, **kwargs)

    @public
    def run(
        self,
        models: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        select: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> DbtCliOutput:
        """Run the ``run`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            models (List[str], optional): the models to include in the run.
            exclude (List[str]), optional): the models to exclude from the run.
            select (List[str], optional): the models to include in the run.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("run", models=models, exclude=exclude, select=select, **kwargs)

    @public
    def snapshot(
        self,
        select: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> DbtCliOutput:
        """Run the ``snapshot`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            select (List[str], optional): the snapshots to include in the run.
            exclude (List[str], optional): the snapshots to exclude from the run.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("snapshot", select=select, exclude=exclude, **kwargs)

    @public
    def test(
        self,
        models: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        data: bool = True,
        schema: bool = True,
        select: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> DbtCliOutput:
        """Run the ``test`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            models (List[str], optional): the models to include in testing.
            exclude (List[str], optional): the models to exclude from testing.
            data (bool, optional): If ``True`` (default), then run data tests.
            schema (bool, optional): If ``True`` (default), then run schema tests.
            select (List[str], optional): the models to include in testing.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        if data and schema:
            # do not include these arguments if both are True, as these are deprecated in later
            # versions of dbt, and for older versions the functionality is the same regardless of
            # if both are set or neither are set.
            return self.cli("test", models=models, exclude=exclude, select=select, **kwargs)
        return self.cli(
            "test",
            models=models,
            exclude=exclude,
            data=data,
            schema=schema,
            select=select,
            **kwargs,
        )

    @public
    def seed(
        self,
        show: bool = False,
        select: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> DbtCliOutput:
        """Run the ``seed`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            show (bool, optional): If ``True``, then show a sample of the seeded data in the
                response. Defaults to ``False``.
            select (List[str], optional): the snapshots to include in the run.
            exclude (List[str], optional): the snapshots to exclude from the run.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("seed", show=show, select=select, exclude=exclude, **kwargs)

    @public
    def ls(
        self,
        select: Optional[Sequence[str]] = None,
        models: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> DbtCliOutput:
        """Run the ``ls`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            select (List[str], optional): the resources to include in the output.
            models (List[str], optional): the models to include in the output.
            exclude (List[str], optional): the resources to exclude from the output.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("ls", select=select, models=models, exclude=exclude, **kwargs)

    @public
    def build(self, select: Optional[Sequence[str]] = None, **kwargs) -> DbtCliOutput:
        """Run the ``build`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            select (List[str], optional): the models/resources to include in the run.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("build", select=select, **kwargs)

    @public
    def freshness(self, select: Optional[Sequence[str]] = None, **kwargs) -> DbtCliOutput:
        """Run the ``source snapshot-freshness`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            select (List[str], optional): the sources to include in the run.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("source snapshot-freshness", select=select, **kwargs)

    @public
    def generate_docs(self, compile_project: bool = False, **kwargs) -> DbtCliOutput:
        """Run the ``docs generate`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            compile_project (bool, optional): If true, compile the project before generating a catalog.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli("docs generate", compile=compile_project, **kwargs)

    @public
    def run_operation(
        self, macro: str, args: Optional[Mapping[str, Any]] = None, **kwargs
    ) -> DbtCliOutput:
        """Run the ``run-operation`` command on a dbt project. kwargs are passed in as additional parameters.

        Args:
            macro (str): the dbt macro to invoke.
            args (Dict[str, Any], optional): the keyword arguments to be supplied to the macro.

        Returns:
            DbtCliOutput: An instance of :class:`DbtCliOutput<dagster_dbt.DbtCliOutput>` containing
                parsed log output as well as the contents of run_results.json (if applicable).
        """
        return self.cli(f"run-operation {macro}", args=args, **kwargs)

    @public
    def get_run_results_json(self, **kwargs) -> Optional[Mapping[str, Any]]:
        """Get a parsed version of the run_results.json file for the relevant dbt project.

        Returns:
            Dict[str, Any]: dictionary containing the parsed contents of the manifest json file
                for this dbt project.
        """
        project_dir = kwargs.get("project_dir", self.default_flags["project-dir"])
        target_path = kwargs.get("target_path", self._target_path)
        return parse_run_results(project_dir, target_path)

    @public
    def remove_run_results_json(self, **kwargs):
        """Remove the run_results.json file from previous runs (if it exists)."""
        project_dir = kwargs.get("project_dir", self.default_flags["project-dir"])
        target_path = kwargs.get("target_path", self._target_path)
        remove_run_results(project_dir, target_path)

    @public
    def get_manifest_json(self, **kwargs) -> Optional[Mapping[str, Any]]:
        """Get a parsed version of the manifest.json file for the relevant dbt project.

        Returns:
            Dict[str, Any]: dictionary containing the parsed contents of the manifest json file
                for this dbt project.
        """
        project_dir = kwargs.get("project_dir", self.default_flags["project-dir"])
        target_path = kwargs.get("target_path", self._target_path)
        return parse_manifest(project_dir, target_path)


@resource(
    config_schema=Permissive(
        {
            k.replace("-", "_"): v
            for k, v in dict(
                **CLI_COMMON_FLAGS_CONFIG_SCHEMA, **CLI_COMMON_OPTIONS_CONFIG_SCHEMA
            ).items()
        }
    )
)
def dbt_cli_resource(context) -> DbtCliResource:
    """This resource issues dbt CLI commands against a configured dbt project."""
    # set of options in the config schema that are not flags
    non_flag_options = {k.replace("-", "_") for k in CLI_COMMON_OPTIONS_CONFIG_SCHEMA}
    # all config options that are intended to be used as flags for dbt commands
    default_flags = {k: v for k, v in context.resource_config.items() if k not in non_flag_options}
    return DbtCliResource(
        executable=context.resource_config["dbt_executable"],
        default_flags=default_flags,
        warn_error=context.resource_config["warn_error"],
        ignore_handled_error=context.resource_config["ignore_handled_error"],
        target_path=context.resource_config["target_path"],
        logger=context.log,
        docs_url=context.resource_config.get("docs_url"),
        capture_logs=context.resource_config["capture_logs"],
        json_log_format=context.resource_config["json_log_format"],
        debug=context.resource_config["debug"],
    )
