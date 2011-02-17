"""
Advantage database backend for Django.

Requires adsdb
"""

import re

try:
    import adsdb as Database
except ImportError, e:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("Error loading adsdb module: %s" % e)

from django.db import utils
from django.conf import settings
from django.db.backends import *
from django.db.backends.signals import connection_created
from adsdb_django.client import DatabaseClient
from adsdb_django.creation import DatabaseCreation
from adsdb_django.introspection import DatabaseIntrospection
from adsdb_django.validation import DatabaseValidation
from adsdb import ads_typecast_timestamp, ads_typecast_date, ads_typecast_time

from django.utils.safestring import SafeString, SafeUnicode

Database.register_converter(Database.DT_TIMESTAMP, ads_typecast_timestamp)
Database.register_converter(Database.DT_DATE, ads_typecast_date)
Database.register_converter(Database.DT_TIME, ads_typecast_time)
Database.register_converter(Database.DT_DECIMAL, util.typecast_decimal)
Database.register_converter(Database.DT_BIT, lambda x: bool(x))


class CursorWrapper(object):
    """
    A thin wrapper around adsdb's normal cursor class so that we can catch
    particular exception instances and reraise them with the right types.

    Implemented as a wrapper, rather than a subclass, so that we aren't stuck
    to the particular underlying representation returned by Connection.cursor().
    """
    codes_for_integrityerror = (1048,)

    def __init__(self, cursor):
        self.cursor = cursor

    def __del__(self):
        if self.cursor:
            self.cursor.close()
            self.cursor = None

    def convert_query(self, query, num_params):
        """
        Django uses "format" style placeholders, but Advantage uses "qmark" style.
        This fixes it -- but note that if you want to use a literal "%s" in a query,
        you'll need to use "%%s".
        """
        return query % tuple("?" * num_params)

    def execute(self, query, args=()):
        try:
            try:
                if args != None:
                    query = self.convert_query(query, len(args))
                ret = self.cursor.execute(query, args)
                return ret
            except Database.OperationalError, e:
                # Map some error codes to IntegrityError, since they seem to be
                # misclassified and Django would prefer the more logical place.
                if e[0] in self.codes_for_integrityerror:
                    raise Database.IntegrityError(tuple(e))
                raise
        except Database.IntegrityError, e:
            raise utils.IntegrityError(e)
        except Database.DatabaseError, e:
            raise utils.DatabaseError(e)
    def executemany(self, query, args):
        try:
            try:
                if len(args) > 0:
                    query = self.convert_query(query, len(args[0]))
                    ret = self.cursor.executemany(query, args)
                    return ret
                else:
                    return None
            except Database.OperationalError, e:
                # Map some error codes to IntegrityError, since they seem to be
                # misclassified and Django would prefer the more logical place.
                if e[0] in self.codes_for_integrityerror:
                    raise Database.IntegrityError(tuple(e))
                raise
        except Database.IntegrityError, e:
            raise utils.IntegrityError(e)
        except Database.DatabaseError, e:
            raise utils.DatabaseError(e)

    def __getattr__(self, attr):
        if attr in self.__dict__:
            return self.__dict__[attr]
        else:
            return getattr(self.cursor, attr)

    def __iter__(self):
        return iter(self.cursor.fetchall())

class DatabaseFeatures(BaseDatabaseFeatures):
    empty_fetchmany_value = []
    update_can_self_select = False
    allows_group_by_pk = False
    related_fields_match_type = True
    uses_custom_query_class = True
    interprets_empty_strings_as_nulls = True

class DatabaseOperations(BaseDatabaseOperations):
    compiler_module = "adsdb_django.compiler"

    def date_extract_sql(self, lookup_type, field_name):
        """
        Given a lookup_type of 'year', 'month' or 'day', returns the SQL that
        extracts a value from the given date field field_name.
        """
        if lookup_type == 'week_day':
            # Returns an integer, 1-7, Sunday=1
            return "DAYOFWEEK(%s)" % field_name

        elif lookup_type == 'day':
            return "DAYOFMONTH(%s)" % field_name

        else:
            # YEAR(), HOUR(), MINUTE(), SECOND() functions
            return "%s(%s)" % (lookup_type.upper(), field_name)

    def date_trunc_sql(self, lookup_type, field_name):
        """
        Given a lookup_type of 'year', 'month', 'day', 'hour', 'minute', or 'second' 
        returns the SQL that truncates the given date/time/timestamp field field_name 
        to a TIMESTAMP object with only the given specificity.
        """
        fields = ['year', 'month', 'day', 'hour', 'minute', 'second']
        format = ('EXTRACT(year FROM %s),' % field_name,
                  'EXTRACT(month FROM %s),' % field_name,
                  'EXTRACT(day FROM %s),' % field_name,
                  'EXTRACT(hour FROM %s),' % field_name,
                  'EXTRACT(minute FROM %s),' % field_name,
                  'EXTRACT(second FROM %s)' % field_name)
        format_def = ('0,', '1,', '1,', '0,', '0,', '0')
        try:
            i = fields.index(lookup_type) + 1
        except ValueError:
            sql = field_name
        else:
            format_str = ''.join([f for f in format[:i]] + [f for f in format_def[i:]])
            sql = "CREATETIMESTAMP( %s, 0 )" % format_str  # Milliseconds not provided so always zero
        return sql

    def drop_foreignkey_sql(self):
        """
        Returns the SQL command that drops a foreign key.
        """
        # ADSDB not implementing foreign key relationships in django
        return None

    def force_no_ordering(self):
        """
        Advantage returns records in natural order by default
        """
        return None

    def fulltext_search_sql(self, field_name):
        """
        Returns the SQL WHERE clause to use in order to perform a full-text
        search of the given field_name. Note that the resulting string should
        contain a '%s' placeholder for the value being searched against.
        """
        return 'CONTAINS(%s, %%s)' % field_name

    def last_insert_id(self, cursor, table_name, pk_name):
        cursor.execute('SELECT LASTAUTOINC( connection ) from system.iota')
        return cursor.fetchone()[0]
    
    def max_name_length(self):
        """
        Returns the maximum length of table and column names, or None if there
        is no limit.
        """
        # ADS has a maximum of 128 for column names, and 255 for table names
        return 128

    def no_limit_value(self):
        """
        Returns the value to use for the LIMIT when we are wanting "LIMIT
        infinity". Returns None if the limit clause can be omitted in this case.
        """
        return None

    def prep_for_iexact_query(self, x):
        return x

    def query_class(self, DefaultQueryClass):
        """
        Given the default Query class, returns a custom Query class
        to use for this backend. Returns None if a custom Query isn't used.
        See also BaseDatabaseFeatures.uses_custom_query_class, which regulates
        whether this method is called at all.
        """
        return query.query_class(DefaultQueryClass)

    def quote_name(self, name):
        """
        Returns a double quoted version of the given table, index or column name. Does
        not quote the given name if it's already been quoted.
        """
        if name.startswith('"') and name.endswith('"'):
            return name # Quoting once is enough.
        return '"%s"' % name

    def squote_name(self, name):
        """
        Returns a single quoted version of the given table, index or column name. Does
        not quote the given name if it's already been quoted.
        This version is primarily for use with system procedures.
        """
        if name.startswith("'") and name.endswith("'"):
            return name # Quoting once is enough.
        return "'%s'" % name

    def regex_lookup(self, lookup_type):
        """
        Returns the string to use in a query when performing regular expression
        lookups (using "regex" or "iregex"). The resulting string should
        contain a '%s' placeholder for the column being searched against.
        """
        raise NotImplementedError("Advantage does not support regular expressions")

    def random_function_sql(self):
        """
        Returns a SQL expression that returns a random value.
        """
        return 'RAND()'

    def savepoint_create_sql(self, sid):
        """
        Returns the SQL for starting a new savepoint. Only required if the
        "uses_savepoints" feature is True. The "sid" parameter is a string
        for the savepoint id.
        """
        return 'SAVEPOINT ' + sid

    def savepoint_commit_sql(self, sid):
        """
        Returns the SQL for committing the given savepoint.
        """
        return 'COMMIT'

    def savepoint_rollback_sql(self, sid):
        """
        Returns the SQL for rolling back the given savepoint.
        """
        return 'ROLLBACK TO SAVEPOINT ' + sid

    def sql_flush(self, style, tables, sequences):
        """
        Returns a list of SQL statements required to remove all data from
        the given database tables (without actually removing the tables
        themselves).
        """
        if tables:
            sql = ''
            # Zap table would work, but DELETE FROM then PACK works if RI is enabled
            # Use sp_PackTable to reset autoinc values, fast since all records are gone
            # UNRESOLVED PF - pack may be slow if the table is large, better way to do this?
            for table in tables:
                sql.append('DELETE FROM %s;' % self.quote_name(table))
                sql.append('EXECUTE PROCEDURE sp_PackTable( %s );' % self.quote_name(table))

            return sql

    def value_to_db_datetime(self, value):
        if value is None:
            return None

        # Advantage doesn't support tz-aware datetimes
        if value.tzinfo is not None:
            raise ValueError("Advantage backend does not support timezone-aware datetimes.")

        return unicode(value)

    def value_to_db_time(self, value):
        if value is None:
            return None

        # Advantage doesn't support tz-aware datetimes
        if value.tzinfo is not None:
            raise ValueError("Advantage backend does not support timezone-aware datetimes.")

        return unicode(value)

class DatabaseWrapper(BaseDatabaseWrapper):
    operators = {
        'exact': '= %s',
        'iexact': '= %s',
        'contains': "LIKE %s ESCAPE '\\'",
        'icontains': "LIKE %s ESCAPE '\\'",
        'regex': 'REGEXP %s',
        'iregex': 'REGEXP %s',
        'gt': '> %s',
        'gte': '>= %s',
        'lt': '< %s',
        'lte': '<= %s',
        'startswith': "LIKE %s ESCAPE '\\'",
        'istartswith': "LIKE %s ESCAPE '\\'",
        'endswith': "LIKE %s ESCAPE '\\'",
        'iendswith': "LIKE %s ESCAPE '\\'"
    }

    def __init__(self, *args, **kwargs):
        super(DatabaseWrapper, self).__init__(*args, **kwargs)

        self.server_version = None
        self.features = DatabaseFeatures()
        self.ops = DatabaseOperations()
        self.client = DatabaseClient(self)
        self.creation = DatabaseCreation(self)
        self.introspection = DatabaseIntrospection(self)
        self.validation = DatabaseValidation(self)

    def _valid_connection(self):
        if self.connection is not None:
            try:
                self.connection.con()
                return True
            except InterfaceError:
                self.connection.close()
                self.connection = None
        return False

    def _cursor(self):
        if not self._valid_connection():
            kwargs = {}
            links = {}
            settings_dict = self.settings_dict
            if settings_dict['USER']:
                kwargs['UserID'] = settings_dict['USER']
            if settings_dict['NAME']:
                kwargs['DataSource'] = settings_dict['NAME']
            if settings_dict['PASSWORD']:
                kwargs['PASSWORD'] = settings_dict['PASSWORD']
            self.connection = Database.connect(**kwargs)
            connection_created.send(sender=self.__class__)
        cursor = CursorWrapper(self.connection.cursor())

        return cursor

    def _rollback(self):
        try:
            BaseDatabaseWrapper._rollback(self)
        except Database.NotSupportedError:
            pass
