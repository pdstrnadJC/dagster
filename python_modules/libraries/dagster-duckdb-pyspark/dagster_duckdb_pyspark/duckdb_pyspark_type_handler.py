from typing import Optional, Sequence, Type

import pyspark
import pyspark.sql
from dagster import InputContext, MetadataValue, OutputContext, TableColumn, TableSchema
from dagster._core.storage.db_io_manager import DbTypeHandler, TableSlice
from dagster_duckdb.io_manager import (
    DuckDbClient,
    DuckDBIOManager,
    build_duckdb_io_manager,
)
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType


class DuckDBPySparkTypeHandler(DbTypeHandler[pyspark.sql.DataFrame]):
    """Stores PySpark DataFrames in DuckDB.

    To use this type handler, return it from the ``type_handlers` method of an I/O manager that inherits from ``DuckDBIOManager``.

    Example:
        .. code-block:: python

            from dagster_duckdb import DuckDBIOManager
            from dagster_duckdb_pyspark import DuckDBPySparkTypeHandler

            class MyDuckDBIOManager(DuckDBIOManager):
                @staticmethod
                def type_handlers() -> Sequence[DbTypeHandler]:
                    return [DuckDBPySparkTypeHandler()]

            @asset(
                key_prefix=["my_schema"]  # will be used as the schema in duckdb
            )
            def my_table() -> pyspark.sql.DataFrame:  # the name of the asset will be the table name
                ...

            defs = Definitions(
                assets=[my_table],
                resources={"io_manager": MyDuckDBIOManager(database="my_db.duckdb")}
            )
    """

    def handle_output(
        self,
        context: OutputContext,
        table_slice: TableSlice,
        obj: pyspark.sql.DataFrame,
        connection,
    ):
        """Stores the given object at the provided filepath."""
        pd_df = obj.toPandas()  # noqa: F841
        connection.execute(
            f"create table if not exists {table_slice.schema}.{table_slice.table} as select * from"
            " pd_df;"
        )
        if not connection.fetchall():
            # table was not created, therefore already exists. Insert the data
            connection.execute(
                f"insert into {table_slice.schema}.{table_slice.table} select * from pd_df"
            )

        context.add_output_metadata(
            {
                "row_count": obj.count(),
                "dataframe_columns": MetadataValue.table_schema(
                    TableSchema(
                        columns=[
                            TableColumn(name=name, type=str(dtype)) for name, dtype in obj.dtypes
                        ]
                    )
                ),
            }
        )

    def load_input(
        self, context: InputContext, table_slice: TableSlice, connection
    ) -> pyspark.sql.DataFrame:
        """Loads the return of the query as the correct type."""
        spark = SparkSession.builder.getOrCreate()
        if table_slice.partition_dimensions and len(context.asset_partition_keys) == 0:
            return spark.createDataFrame([], StructType([]))

        pd_df = connection.execute(DuckDbClient.get_select_statement(table_slice)).fetchdf()
        return spark.createDataFrame(pd_df)

    @property
    def supported_types(self):
        return [pyspark.sql.DataFrame]


duckdb_pyspark_io_manager = build_duckdb_io_manager(
    [DuckDBPySparkTypeHandler()], default_load_type=pyspark.sql.DataFrame
)
duckdb_pyspark_io_manager.__doc__ = """
An I/O manager definition that reads inputs from and writes PySpark DataFrames to DuckDB. When
using the duckdb_pyspark_io_manager, any inputs and outputs without type annotations will be loaded
as PySpark DataFrames.

Returns:
    IOManagerDefinition

Examples:

    .. code-block:: python

        from dagster_duckdb_pyspark import duckdb_pyspark_io_manager

        @asset(
            key_prefix=["my_schema"]  # will be used as the schema in DuckDB
        )
        def my_table() -> pyspark.sql.DataFrame:  # the name of the asset will be the table name
            ...

        @repository
        def my_repo():
            return with_resources(
                [my_table],
                {"io_manager": duckdb_pyspark_io_manager.configured({"database": "my_db.duckdb"})}
            )

    If you do not provide a schema, Dagster will determine a schema based on the assets and ops using
    the I/O Manager. For assets, the schema will be determined from the asset key.
    For ops, the schema can be specified by including a "schema" entry in output metadata. If "schema" is not provided
    via config or on the asset/op, "public" will be used for the schema.

    .. code-block:: python

        @op(
            out={"my_table": Out(metadata={"schema": "my_schema"})}
        )
        def make_my_table() -> pyspark.sql.DataFrame:
            # the returned value will be stored at my_schema.my_table
            ...

    To only use specific columns of a table as input to a downstream op or asset, add the metadata "columns" to the
    In or AssetIn.

    .. code-block:: python

        @asset(
            ins={"my_table": AssetIn("my_table", metadata={"columns": ["a"]})}
        )
        def my_table_a(my_table: pyspark.sql.DataFrame) -> pyspark.sql.DataFrame:
            # my_table will just contain the data from column "a"
            ...

"""


class DuckDBPySparkIOManager(DuckDBIOManager):
    """An I/O manager definition that reads inputs from and writes PySpark DataFrames to DuckDB. When
    using the DuckDBPySparkIOManager, any inputs and outputs without type annotations will be loaded
    as PySpark DataFrames.

    Returns:
        IOManagerDefinition

    Examples:
        .. code-block:: python

            from dagster_duckdb_pyspark import DuckDBPySparkIOManager

            @asset(
                key_prefix=["my_schema"]  # will be used as the schema in DuckDB
            )
            def my_table() -> pyspark.sql.DataFrame:  # the name of the asset will be the table name
                ...

            defs = Definitions(
                assets=[my_table],
                resources={"io_manager": DuckDBPySparkIOManager(database="my_db.duckdb")}
            )

        If you do not provide a schema, Dagster will determine a schema based on the assets and ops using
        the I/O Manager. For assets, the schema will be determined from the asset key, as in the above example.
        For ops, the schema can be specified by including a "schema" entry in output metadata. If "schema" is not provided
        via config or on the asset/op, "public" will be used for the schema.

        .. code-block:: python

            @op(
                out={"my_table": Out(metadata={"schema": "my_schema"})}
            )
            def make_my_table() -> pyspark.sql.DataFrame:
                # the returned value will be stored at my_schema.my_table
                ...

        To only use specific columns of a table as input to a downstream op or asset, add the metadata "columns" to the
        In or AssetIn.

        .. code-block:: python

            @asset(
                ins={"my_table": AssetIn("my_table", metadata={"columns": ["a"]})}
            )
            def my_table_a(my_table: pyspark.sql.DataFrame) -> pyspark.sql.DataFrame:
                # my_table will just contain the data from column "a"
                ...

    """

    @staticmethod
    def type_handlers() -> Sequence[DbTypeHandler]:
        return [DuckDBPySparkTypeHandler()]

    @staticmethod
    def default_load_type() -> Optional[Type]:
        return pyspark.sql.DataFrame
