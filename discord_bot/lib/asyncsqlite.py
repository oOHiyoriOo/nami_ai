import aiosqlite

class AsyncSQLite:
    def __init__(self, db_path="database.db", schema=None):
        """
        Initializes the async SQLite database connection with a schema.
        :param db_path: Path to the SQLite database file.
        :param schema: Dictionary defining the tables and their structures.
        """
        self.db_path = db_path
        self.schema = schema or {}
        self.connection = None

    async def initialize(self):
        """
        Creates tables based on the schema provided during initialization.
        """
        if not self.schema:
            raise ValueError("No schema provided for database initialization.")
        async with aiosqlite.connect(self.db_path) as db:
            for table_name, table_schema in self.schema.items():
                query = f"CREATE TABLE IF NOT EXISTS {table_name} ({table_schema})"
                await db.execute(query)
            await db.commit()

    async def connect(self):
        """
        Opens a persistent connection to the database.
        """
        if not self.connection:
            self.connection = await aiosqlite.connect(self.db_path)

    async def close(self):
        """
        Closes the persistent database connection.
        """
        if self.connection:
            await self.connection.close()
            self.connection = None

    async def execute(self, query, params=None):
        """
        Executes a generic query and commits changes if needed.
        :param query: The SQL query to execute.
        :param params: Parameters to bind to the query.
        """
        await self.connect()
        async with self.connection.execute(query, params or ()) as cursor:
            await self.connection.commit()
            return cursor

    async def fetch_all(self, query, params=None):
        """
        Executes a query and returns all results.
        :param query: The SQL query to execute.
        :param params: Parameters to bind to the query.
        :return: List of rows.
        """
        await self.connect()
        async with self.connection.execute(query, params or ()) as cursor:
            return await cursor.fetchall()

    async def fetch_one(self, query, params=None):
        """
        Executes a query and returns a single result.
        :param query: The SQL query to execute.
        :param params: Parameters to bind to the query.
        :return: Single row or None.
        """
        await self.connect()
        async with self.connection.execute(query, params or ()) as cursor:
            return await cursor.fetchone()

    async def insert(self, table, data):
        """
        Inserts data into a specific table.
        :param table: The name of the table.
        :param data: Dictionary of column-value pairs.
        """
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data.values()])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        await self.execute(query, tuple(data.values()))

    async def delete(self, table, condition, params=None):
        """
        Deletes rows from a table based on a condition.
        :param table: The name of the table.
        :param condition: The WHERE condition for deletion.
        :param params: Parameters to bind to the condition.
        """
        query = f"DELETE FROM {table} WHERE {condition}"
        await self.execute(query, params or ())

    async def update(self, table, data, condition, params=None):
        """
        Updates rows in a table based on a condition.
        :param table: The name of the table.
        :param data: Dictionary of column-value pairs to update.
        :param condition: The WHERE condition for updating.
        :param params: Parameters to bind to the condition.
        """
        set_clause = ", ".join([f"{col} = ?" for col in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {condition}"
        await self.execute(query, (*data.values(), *(params or ())))