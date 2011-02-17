from django.db.backends import BaseDatabaseIntrospection
from adsdb import ProgrammingError, OperationalError
import re
import adsdb


foreign_key_re = re.compile(r"\sCONSTRAINT `[^`]*` FOREIGN KEY \(`([^`]*)`\) REFERENCES `([^`]*)` \(`([^`]*)`\)")

class DatabaseIntrospection(BaseDatabaseIntrospection):
    data_types_reverse = { adsdb.DT_DATE         : 'DateField',
                           adsdb.DT_TIME         : 'TimeField',
                           adsdb.DT_TIMESTAMP    : 'DateTimeField',
                           adsdb.DT_VARCHAR      : 'CharField',
                           adsdb.DT_FIXCHAR      : 'CharField',
                           adsdb.DT_LONGVARCHAR  : 'TextField',
                           adsdb.DT_STRING       : 'CharField',
                           adsdb.DT_DOUBLE       : 'FloatField',
                           adsdb.DT_FLOAT        : 'FloatField',
                           adsdb.DT_DECIMAL      : 'DecimalField',
                           adsdb.DT_INT          : 'IntegerField',
                           adsdb.DT_SMALLINT     : 'IntegerField',
                           adsdb.DT_BINARY       : 'BlobField',
                           adsdb.DT_LONGBINARY   : 'BlobField',
                           adsdb.DT_TINYINT      : 'IntegerField',
                           adsdb.DT_BIGINT       : 'BigIntegerField',
                           adsdb.DT_UNSINT       : 'IntegerField',
                           adsdb.DT_UNSSMALLINT  : 'IntegerField',
                           adsdb.DT_UNSBIGINT    : 'BigIntegerField',
                           adsdb.DT_BIT          : 'NullBooleanField',
                           adsdb.DT_LONGNVARCHAR : 'TextField',
                           adsdb.DT_NSTRING      : 'CharField',
                           adsdb.DT_NFIXCHAR     : 'CharField',
                           adsdb.DT_NVARCHAR     : 'CharField',
                         }

    def get_table_list(self, cursor):
        "Returns a list of table names in the current database."
        cursor.execute("SELECT name FROM system.tables")
        return [row[0].strip() for row in cursor.fetchall()]

    def get_table_description(self, cursor, table_name):
        "Returns a description of the table, with the DB-API cursor.description interface."
        cursor.execute("SELECT TOP 1 * FROM %s" % self.connection.ops.quote_name(table_name))
        return cursor.description

    def _name_to_index(self, cursor, table_name):
        """
        Returns a dictionary of {field_name: field_index} for the given table.
        Indexes are 0-based.
        """
        return dict([(d[0], i) for i, d in enumerate(self.get_table_description(cursor, table_name))])

    def get_relations(self, cursor, table_name):
        "ADSDB doesn't support relations in django."
        raise NotImplementedError

    def get_indexes(self, cursor, table_name):
        """
        Returns a dictionary of fieldname -> infodict for the given table,
        where each infodict is in the format:
            {'primary_key': boolean representing whether it's the primary key,
             'unique': boolean representing whether it's a unique index}
        """
        cursor.execute("""
        SELECT ix.name,
               ix.index_expression,
               IIF( ix.index_options & 1 = 1, 1, 0 ) as unq,
               (SELECT IIF( table_primary_key = ix.name, 1, 0 )
        FROM system.tables
        WHERE name = '%s') as pk
        FROM system.indexes ix WHERE parent = '%s'
        """ % (table_name, table_name))

        indexes = {}
        for name, expr, unique, pk in cursor.fetchall():
            indexes[expr.strip()] = {
                'primary_key': (pk == 1),
                'unique': (unique == 1) }

        return indexes

    def get_field_type(self, data_type, row):
        """
        Return the field type given the cursor row description
        """
        return DatabaseIntrospection.data_types_reverse[row[7]]


